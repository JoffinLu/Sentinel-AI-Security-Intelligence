import zipfile, pathlib

docx = pathlib.Path('归档/技术报告/AI安全知识情报系统技术报告.docx')
src = {p.name: p.stat().st_size for p in pathlib.Path('归档/技术报告/插图').glob('图*.png')}
with zipfile.ZipFile(docx) as z:
    media = {n.split('/')[-1]: z.getinfo(n).file_size
             for n in z.namelist() if n.startswith('word/media/')}

print('docx media count:', len(media))
ok = 0
for name, size in sorted(src.items(), key=lambda kv: int(kv[0].split('_')[0][1:])):
    hit = [m for m, s in media.items() if s == size]
    if hit:
        ok += 1
    tag = ','.join(hit) if hit else 'MISSING'
    print(f'{name:26s} {size:8d}  -> {tag}')
print(f'\nembedded {ok}/{len(src)} figures')