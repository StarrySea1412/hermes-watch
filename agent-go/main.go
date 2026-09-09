// Hermes Watch outbound agent — single static binary, stdlib only.
// Reads /proc directly (only df/systemctl shell out), pushes signed metrics
// + extras to the panel every interval. Read-only on the host.
//
// Build (from this directory):
//   GOOS=linux   GOARCH=amd64 go build -ldflags "-s -w" -o hermes-watch-agent-linux-amd64
//   GOOS=windows GOARCH=amd64 go build -ldflags "-s -w" -o hermes-watch-agent.exe
//
// Run on the monitored host:
//   HW_URL=http://panel:8800 HW_TOKEN=hw_xxx ./hermes-watch-agent
// HW_TOKEN — per-host token from the panel's enrollment page; it doubles as
// the HMAC-SHA256 key that signs every push (replay-protected by timestamp).
package main

import (
	"bytes"
	"crypto/hmac"
	"crypto/sha256"
	"crypto/x509"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"
)

type Extra struct {
	TopProc      string   `json:"top_proc,omitempty"`
	FailedSvcs   []string `json:"failed_services,omitempty"`
	CertDaysLeft *int     `json:"cert_days_left,omitempty"`
	DiskIORead   float64  `json:"disk_io_read,omitempty"`  // KiB/s（采样窗口均值）
	DiskIOWrite  float64  `json:"disk_io_write,omitempty"` // KiB/s
	TempC        float64  `json:"temp_c,omitempty"`        // 主温度传感器 °C（无传感器时为 0 不上报）
}

type Payload struct {
	TS    int64   `json:"ts"`
	CPU   float64 `json:"cpu"`
	Mem   float64 `json:"mem"`
	Swap  float64 `json:"swap"`  // swap 使用率 %（无 swap 时为 0）
	Disk  float64 `json:"disk"`
	Load1 float64 `json:"load1"`
	NetIn float64 `json:"net_in"`
	NetOut float64 `json:"net_out"`
	Extra *Extra  `json:"extra,omitempty"`
}

var (
	panelURL string
	token    string
	interval time.Duration
)

func env(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

// ---------- /proc readers (Linux) ----------

func readCPULoad() (cpu float64, load1 float64) {
	if data, err := os.ReadFile("/proc/stat"); err == nil {
		for _, line := range strings.Split(string(data), "\n") {
			if strings.HasPrefix(line, "cpu ") {
				fields := strings.Fields(line)
				var total, idle float64
				for i, f := range fields[1:] {
					v, _ := strconv.ParseFloat(f, 64)
					total += v
					if i == 3 || i == 4 { // idle + iowait
						idle += v
					}
				}
				if total > 0 {
					cpu = (1 - idle/total) * 100
				}
				break
			}
		}
	}
	if data, err := os.ReadFile("/proc/loadavg"); err == nil {
		fmt.Sscanf(string(data), "%f", &load1)
	}
	return
}

func readMem() (mem, swap float64) {
	data, err := os.ReadFile("/proc/meminfo")
	if err != nil {
		return
	}
	var total, avail, swapTotal, swapFree float64
	for _, line := range strings.Split(string(data), "\n") {
		switch {
		case strings.HasPrefix(line, "MemTotal:"):
			fmt.Sscanf(line, "MemTotal: %f kB", &total)
		case strings.HasPrefix(line, "MemAvailable:"):
			fmt.Sscanf(line, "MemAvailable: %f kB", &avail)
		case strings.HasPrefix(line, "SwapTotal:"):
			fmt.Sscanf(line, "SwapTotal: %f kB", &swapTotal)
		case strings.HasPrefix(line, "SwapFree:"):
			fmt.Sscanf(line, "SwapFree: %f kB", &swapFree)
		}
	}
	if total > 0 {
		mem = (total - avail) / total * 100
	}
	if swapTotal > 0 {
		swap = (swapTotal - swapFree) / swapTotal * 100
	}
	return
}

func readDisk() float64 {
	out := sh("df -P -x tmpfs -x devtmpfs")
	max := 0.0
	for _, line := range strings.Split(out, "\n") {
		fields := strings.Fields(line)
		if len(fields) < 5 || !strings.HasSuffix(fields[4], "%") {
			continue
		}
		pct, err := strconv.ParseFloat(strings.TrimSuffix(fields[4], "%"), 64)
		if err == nil && pct > max {
			max = pct
		}
	}
	return max
}

func readNet() (in, out float64) {
	data, err := os.ReadFile("/proc/net/dev")
	if err != nil {
		return
	}
	for _, line := range strings.Split(string(data), "\n") {
		idx := strings.Index(line, ":")
		if idx < 0 {
			continue
		}
		iface := strings.TrimSpace(line[:idx])
		if iface == "lo" {
			continue
		}
		fields := strings.Fields(line[idx+1:])
		if len(fields) >= 9 {
			rx, _ := strconv.ParseFloat(fields[0], 64)
			tx, _ := strconv.ParseFloat(fields[8], 64)
			in += rx
			out += tx
		}
	}
	return
}

func uptimeSeconds() float64 {
	data, err := os.ReadFile("/proc/uptime")
	if err != nil {
		return 1
	}
	var up float64
	fmt.Sscanf(string(data), "%f", &up)
	if up <= 0 {
		up = 1
	}
	return up
}

func readTopProcs() string {
	entries, _ := filepath.Glob("/proc/[0-9]*/stat")
	pageKB := float64(os.Getpagesize()) / 1024
	clkTck := 100.0
	uptime := uptimeSeconds()

	type procInfo struct {
		pid, rss int
		pcpu     float64
		cmd      string
	}
	var procs []procInfo
	for _, p := range entries {
		data, err := os.ReadFile(p)
		if err != nil {
			continue
		}
		close := strings.LastIndex(string(data), ")")
		if close < 0 || close+2 > len(data) {
			continue
		}
		fields := strings.Fields(string(data[close+2:]))
		if len(fields) < 22 {
			continue
		}
		pid, _ := strconv.Atoi(filepath.Base(filepath.Dir(p)))
		rssPages, _ := strconv.Atoi(fields[21])
		utime, _ := strconv.ParseFloat(fields[11], 64)
		stime, _ := strconv.ParseFloat(fields[12], 64)
		start, _ := strconv.ParseFloat(fields[19], 64)
		elapsed := uptime - start/clkTck
		pcpu := 0.0
		if elapsed > 0 {
			pcpu = (utime + stime) / clkTck / elapsed * 100
		}
		procs = append(procs, procInfo{
			pid:  pid,
			rss:  int(float64(rssPages) * pageKB),
			pcpu: pcpu,
			cmd:  strings.Trim(fields[0], "()"),
		})
	}
	sort.Slice(procs, func(i, j int) bool { return procs[i].rss > procs[j].rss })
	if len(procs) > 5 {
		procs = procs[:5]
	}
	var b strings.Builder
	b.WriteString("PID     CPU%    RSS-KB  COMMAND\n")
	for _, pr := range procs {
		fmt.Fprintf(&b, "%-6d  %-6.1f  %-6d  %s\n", pr.pid, pr.pcpu, pr.rss, pr.cmd)
	}
	return strings.TrimRight(b.String(), "\n")
}

func readFailedServices() []string {
	out := sh("systemctl list-units --state=failed --no-legend --plain")
	var failed []string
	for _, line := range strings.Split(out, "\n") {
		fields := strings.Fields(line)
		if len(fields) > 0 && strings.HasSuffix(fields[0], ".service") {
			failed = append(failed, fields[0])
		}
	}
	return failed
}

func readCertDays() *int {
	matches, _ := filepath.Glob("/etc/letsencrypt/live/*/cert.pem")
	if len(matches) == 0 {
		return nil
	}
	data, err := os.ReadFile(matches[0])
	if err != nil {
		return nil
	}
	block, _ := pem.Decode(data)
	if block == nil {
		return nil
	}
	cert, err := x509.ParseCertificate(block.Bytes)
	if err != nil {
		return nil
	}
	days := int(time.Until(cert.NotAfter).Hours() / 24)
	return &days
}

// diskIOStats 汇总 /proc/diskstats 的物理盘读写扇区数（排除 loop/ram/dm 等虚拟设备）。
func diskIOStats() (readSec, writeSec float64) {
	data, err := os.ReadFile("/proc/diskstats")
	if err != nil {
		return
	}
	for _, line := range strings.Split(string(data), "\n") {
		fields := strings.Fields(line)
		if len(fields) < 10 {
			continue
		}
		dev := fields[2]
		if strings.HasPrefix(dev, "loop") || strings.HasPrefix(dev, "ram") ||
			strings.HasPrefix(dev, "dm-") || strings.HasPrefix(dev, "zram") ||
			strings.HasPrefix(dev, "sr") || strings.HasPrefix(dev, "fd") {
			continue
		}
		r, _ := strconv.ParseFloat(fields[5], 64)  // 读取扇区数
		w, _ := strconv.ParseFloat(fields[9], 64)  // 写入扇区数
		readSec += r
		writeSec += w
	}
	return
}

// readDiskIO 两次采样求速率（KiB/s）。窗口 = HW_IO_WINDOW_MS（默认 500ms，够稳且几乎无开销）。
func readDiskIO() (readKBs, writeKBs float64) {
	r1, w1 := diskIOStats()
	window := 500 * time.Millisecond
	if n, err := strconv.Atoi(env("HW_IO_WINDOW_MS", "500")); err == nil && n >= 100 && n <= 5000 {
		window = time.Duration(n) * time.Millisecond
	}
	time.Sleep(window)
	r2, w2 := diskIOStats()
	secs := window.Seconds()
	if secs <= 0 {
		return
	}
	// 扇区在 Linux 上恒为 512B；计数可能回绕/重置（重装盘），负值按 0 处理
	readKBs = maxF(0, (r2-r1)*512/1024/secs)
	writeKBs = maxF(0, (w2-w1)*512/1024/secs)
	return
}

// readTemp 找 hwmon 里的主温度（优先 CPU/核心/Tdie/SOC，单位毫摄氏度）。
func readTemp() float64 {
	entries, _ := filepath.Glob("/sys/class/hwmon/hwmon*/temp*_input")
	best, bestPrio := 0.0, 99
	names := map[string]bool{}
	for _, p := range entries {
		labelFile := strings.TrimSuffix(p, "_input") + "_label"
		label := ""
		if b, err := os.ReadFile(labelFile); err == nil {
			label = strings.ToLower(strings.TrimSpace(string(b)))
		}
		name := filepath.Base(filepath.Dir(filepath.Dir(p)))
		key := name + ":" + label
		if names[key] {
			continue // 同一芯片同名传感器只看一次
		}
		names[key] = true
		prio := 50
		switch {
		case strings.Contains(label, "cpu"), strings.Contains(label, "tdie"), strings.Contains(label, "tctl"):
			prio = 0
		case strings.Contains(label, "core"), strings.Contains(label, "soc"), strings.Contains(label, "package"):
			prio = 10
		case strings.Contains(label, "nvme"), strings.Contains(label, "composite"):
			prio = 20
		}
		if prio > bestPrio {
			continue
		}
		b, err := os.ReadFile(p)
		if err != nil {
			continue
		}
		mill, _ := strconv.ParseFloat(strings.TrimSpace(string(b)), 64)
		if mill <= 0 || mill > 150000 { // 0°C 或 >150°C 的读数不可信
			continue
		}
		if prio < bestPrio || best == 0 {
			bestPrio = prio
			best = mill / 1000
		}
	}
	return best
}

func maxF(a, b float64) float64 {
	if a > b {
		return a
	}
	return b
}

// sh runs one of two whitelisted read-only commands (df / systemctl).
func sh(cmd string) string {
	parts := strings.Fields(cmd)
	if len(parts) == 0 || (parts[0] != "df" && parts[0] != "systemctl") {
		return ""
	}
	out, err := exec.Command(parts[0], parts[1:]...).Output()
	if err != nil {
		return ""
	}
	return string(out)
}

// ---------- push ----------

func sign(body []byte, ts int64) string {
	mac := hmac.New(sha256.New, []byte(token))
	mac.Write(body)
	mac.Write([]byte(strconv.FormatInt(ts, 10)))
	return hex.EncodeToString(mac.Sum(nil))
}

func collect() (Payload, string) {
	cpu, load1 := readCPULoad()
	mem, swap := readMem()
	p := Payload{
		TS:    time.Now().Unix(),
		CPU:   round1(cpu),
		Mem:   round1(mem),
		Swap:  round1(swap),
		Disk:  round1(readDisk()),
		Load1: round2(load1),
	}
	p.NetIn, p.NetOut = readNet()
	ex := &Extra{TopProc: readTopProcs(), FailedSvcs: readFailedServices(), CertDaysLeft: readCertDays()}
	if readKBs, writeKBs := readDiskIO(); readKBs > 0 || writeKBs > 0 {
		ex.DiskIORead = round1(readKBs)
		ex.DiskIOWrite = round1(writeKBs)
	}
	if t := readTemp(); t > 0 {
		ex.TempC = round1(t)
	}
	if ex.TopProc != "" || len(ex.FailedSvcs) > 0 || ex.CertDaysLeft != nil ||
		ex.DiskIORead > 0 || ex.DiskIOWrite > 0 || ex.TempC > 0 {
		p.Extra = ex
	}
	body, _ := json.Marshal(p)
	return p, string(body)
}

func push(p Payload, body string) error {
	req, err := http.NewRequest("POST", panelURL+"/api/agent/push/body",
		bytes.NewReader([]byte(body)))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-HW-Token", token)
	req.Header.Set("X-HW-Signature", sign([]byte(body), p.TS))
	req.Header.Set("X-HW-Timestamp", strconv.FormatInt(p.TS, 10))
	client := &http.Client{Timeout: 10 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("panel returned %s", resp.Status)
	}
	return nil
}

func round1(v float64) float64 { return float64(int(v*10+0.5)) / 10 }
func round2(v float64) float64 { return float64(int(v*100+0.5)) / 100 }

func main() {
	panelURL = strings.TrimRight(env("HW_URL", "http://127.0.0.1:8800"), "/")
	token = env("HW_TOKEN", "")
	if token == "" {
		fmt.Fprintln(os.Stderr, "[agent] HW_TOKEN is required (enrollment page generates it)")
		os.Exit(1)
	}
	interval = 60 * time.Second
	if n, err := strconv.Atoi(env("HW_INTERVAL", "60")); err == nil && n >= 5 {
		interval = time.Duration(n) * time.Second
	}
	fmt.Printf("[agent] hermes-watch agent -> %s every %s\n", panelURL, interval)
	for {
		p, body := collect()
		if err := push(p, body); err != nil {
			fmt.Fprintf(os.Stderr, "[agent] push failed: %v (retry next cycle)\n", err)
		} else {
			fmt.Printf("[agent] pushed cpu=%.1f mem=%.1f swap=%.1f disk=%.1f load=%.2f\n", p.CPU, p.Mem, p.Swap, p.Disk, p.Load1)
		}
		time.Sleep(interval)
	}
}
