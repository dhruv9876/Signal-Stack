from pathlib import Path
p = Path("app.py")
src = p.read_text(encoding="utf-8")
Path("app.py.bak").write_text(src, encoding="utf-8")

helper = r"""def show_table(rows):
    rows = [dict(r) for r in (rows or [])]
    if not rows:
        st.caption("Nothing yet.")
        return
    cols = list(rows[0].keys())
    md = "| " + " | ".join(cols) + " |\n"
    md += "| " + " | ".join("---" for _ in cols) + " |\n"
    for r in rows:
        md += "| " + " | ".join(
            str(r.get(c, "")).replace("|", "/").replace("\n", " ") for c in cols) + " |\n"
    st.markdown(md)


"""

changes = 0
if "def show_table(" not in src:
    src = src.replace("def run(label, fn):", helper + "def run(label, fn):", 1)
    changes += 1

old1 = "st.dataframe(comps, use_container_width=True, hide_index=True)"
if old1 in src:
    src = src.replace(old1, "show_table(comps)")
    changes += 1

old2 = "st.write({k: str(v) for k, v in scheduler.next_runs().items()})"
if old2 in src:
    src = src.replace(old2, "st.json({k: str(v) for k, v in scheduler.next_runs().items()})")
    changes += 1

p.write_text(src, encoding="utf-8")
print("changes applied:", changes, "(expected 3)")
print("dataframe left:", "st.dataframe" in src)
