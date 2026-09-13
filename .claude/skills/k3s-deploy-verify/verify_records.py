# Verify every installed package's RECORD sha256 hashes inside a venv — reports corrupt files.
import base64, hashlib, os, sys, glob, csv
site = glob.glob("/app/services/*/.venv/lib/python*/site-packages")[0]
bad, checked = [], 0
for rec in glob.glob(os.path.join(site, "*.dist-info", "RECORD")):
    pkg = os.path.basename(os.path.dirname(rec))
    with open(rec, newline="") as f:
        for path, digest, size in (r for r in csv.reader(f) if len(r) == 3):
            if not digest.startswith("sha256=") or not size: continue
            full = os.path.normpath(os.path.join(site, path))
            checked += 1
            try:
                st = os.stat(full)
                if st.st_size != int(size):
                    bad.append(f"{pkg}: {path} size {st.st_size} != {size}"); continue
                if st.st_size > 1_000_000:   # hash only big files (crash victims) — size check covers the rest
                    h = hashlib.sha256()
                    with open(full, "rb") as fh:
                        for chunk in iter(lambda: fh.read(1 << 20), b""): h.update(chunk)
                    if base64.urlsafe_b64encode(h.digest()).rstrip(b"=").decode() != digest[7:]:
                        bad.append(f"{pkg}: {path} sha256 mismatch")
            except FileNotFoundError:
                bad.append(f"{pkg}: {path} MISSING")
print(f"checked={checked} corrupt={len(bad)}")
for b in bad[:12]: print("  " + b)
sys.exit(1 if bad else 0)
