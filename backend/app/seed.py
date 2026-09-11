"""Demo fleet seeding: 4 hosts with the demo-story chaos modes preloaded.

web-1 healthy / db-1 disk filling / app-1 memory leak / cache-1 suspicious login.
"""
from . import db

DEMO_HOSTS = [
    {"name": "web-1", "hostname": "10.0.0.11", "group_name": "web", "chaos": "busy", "mock": 1},
    {"name": "db-1", "hostname": "10.0.0.12", "group_name": "db", "chaos": "disk_filling", "mock": 1},
    {"name": "app-1", "hostname": "10.0.0.13", "group_name": "app", "chaos": "mem_leak", "mock": 1},
    {"name": "cache-1", "hostname": "10.0.0.14", "group_name": "infra", "chaos": "suspicious_login", "mock": 1},
]


def seed_demo_hosts() -> int:
    for h in DEMO_HOSTS:
        db.execute(
            "INSERT INTO hosts(name,hostname,port,username,secret,group_name,mock,chaos,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (h["name"], h["hostname"], 22, "root", "", h["group_name"], h["mock"], h["chaos"], db.now()))
    return len(DEMO_HOSTS)


def seed_if_empty():
    if db.query_one("SELECT id FROM hosts LIMIT 1"):
        return False
    seed_demo_hosts()
    return True
