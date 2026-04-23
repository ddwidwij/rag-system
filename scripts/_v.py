import json, sys
from pathlib import Path

SUPPORTED = {'.md', '.txt', '.pdf', '.docx', '.doc', '.xlsx', '.csv',
             '.rst', '.dita', '.ditamap', '.pptx', '.xml'}

docs_dir = Path('/Users/raoziyu/Documents/code/VideoCode-main/使用Python构建RAG系统/rag/docs')
files = [f for f in sorted(docs_dir.rglob('*'))
         if f.is_file() and f.suffix.lower() in SUPPORTED]

print(f"共扫描到 {len(files)} 个文档文件")

by_dir = {}
for f in files:
    rel = f.relative_to(docs_dir)
    parts = rel.parts
    top = parts[0] if len(parts) > 1 else '(根)'
    sub = parts[1] if len(parts) > 2 else ''
    key = f"{top}/{sub}" if sub else top
    by_dir.setdefault(key, []).append(f.name)

for dir_key, fnames in sorted(by_dir.items()):
    print(f"  [{dir_key}] {len(fnames)}个: {', '.join(fnames)}")

total = len(files)
has_meta = sum(1 for f in files if Path(str(f)+'.meta.json').exists())
missing = [str(f.relative_to(docs_dir)) for f in files if not Path(str(f)+'.meta.json').exists()]
print(f"\nmeta.json 覆盖率: {has_meta}/{total}")
if missing:
    print("缺少 meta:", missing)

doc_types = {}
for f in files:
    m = Path(str(f)+'.meta.json')
    if m.exists():
        d = json.loads(m.read_text('utf-8'))
        dt = d.get('doc_type','unknown')
        doc_types[dt] = doc_types.get(dt,0)+1
print("\ndoc_type 分布:")
for k,v in sorted(doc_types.items()):
    print(f"  {k}: {v}")
