"""Smoke-test the client: account reads only (no credits spent). Run after login.py."""
from suno_client import SunoClient

sc = SunoClient()
u = sc.me()
print("user:", u.get("display_name"), "| clips:", u.get("total_clips"))
b = sc.billing()
print("credits available:", b["available_credits"],
      f"({b.get('monthly_usage')}/{b.get('monthly_limit')} used)")
print("pending batches:", sc.pending_batches())
print("\nrecent clips:")
for c in sc.feed(limit=5):
    print(f"  {c.get('title')!r:28} {c.get('status'):9} {(c.get('metadata') or {}).get('duration')}s")
print("\nCLIENT OK")
