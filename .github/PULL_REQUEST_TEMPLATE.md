# 提案 PR · Pull Request

## 概要（做了什么 / What）

<!-- 一两句话。风格参照 git log：中文一句话说清 -->

## 自查清单（CONTRIBUTING.md 有细则 / Checklist）

- [ ] `py backend/run_tests.py` 全绿（附通过数：N passed, 0 failed）
- [ ] `npx vitest run` 全绿
- [ ] `ruff check .` 干净
- [ ] 新增 UI 文案补齐 i18n 双语（zh + en）
- [ ] 涉及 settings 的测试已做现场备份/还原（测试库与面板共用）
- [ ] 未破坏设计铁律：只读提议制 / 白名单执行 / AI 外发默认关 / 轻部署
