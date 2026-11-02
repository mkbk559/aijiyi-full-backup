import re
import sys

src = open(r"D:\AIJiYi\docs\architecture.html", encoding="utf-8").read()
for nid in ["outbox", "send_queue", "heart", "dispatch"]:
    m = re.search(r'\{id:"%s".*?\n' % nid, src, re.S)
    if m:
        sys.stdout.buffer.write((m.group(0)[:1400] + "\n---\n").encode("utf-8"))
    else:
        sys.stdout.buffer.write(f"{nid}: NOT FOUND\n".encode("utf-8"))
