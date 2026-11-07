#!/usr/bin/env python3
"""Copy generated card PNGs unchanged and build an offline image gallery."""
import argparse
import base64
import hashlib
import html
import json
from pathlib import Path
import shutil
import struct
from time import perf_counter


def image_key(card):
    fields = ["generation_prompt", "topic", "headline", "card_summary", "labels", "badge", "short_limit"]
    return hashlib.sha256(json.dumps({key: card.get(key) for key in fields}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def render_digest(digest):
    escape = html.escape
    overview = "".join(f"<li>{escape(line)}</li>" for line in digest["overview"])
    sections = []
    markdown = [f"# {digest['title']}", "", f"수집일: {digest['collected_date']} · {digest['room']}", "", digest["scope"], "", "## 핵심 요약", ""]
    markdown.extend(f"- {line}" for line in digest["overview"])
    for section in digest["sections"]:
        items = []
        markdown.extend(["", f"## {section['title']}", ""])
        for item in section["items"]:
            links = " · ".join(f'<a href="{escape(s["url"], quote=True)}" rel="noreferrer">{escape(s["label"])}</a>' for s in item["sources"])
            items.append(f'<div class="digest-item"><h3>{escape(item["title"])}</h3><p>{escape(item["summary"])}</p><p><strong>활용 포인트</strong> · {escape(item["useful"])}</p><p class="note">{escape(item["status"])}</p>' + (f'<p>{links}</p>' if links else '') + '</div>')
            markdown.extend([f"### {item['title']}", "", item["summary"], "", f"활용 포인트: {item['useful']}", "", f"확인 범위: {item['status']}", ""])
            markdown.extend(f"- [{s['label']}]({s['url']})" for s in item["sources"])
        sections.append(f'<section><h2>{escape(section["title"])}</h2>{"".join(items)}</section>')
    content = f'<section class="digest" aria-label="내용 요약"><h2>먼저 읽는 핵심 요약</h2><ul>{overview}</ul><p class="note">{escape(digest["scope"])}</p>{"".join(sections)}<h2>이미지 카드뉴스</h2></section>'
    return content, "\n".join(markdown) + "\n"


def main():
    started = perf_counter()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path.cwd() / "output/cardnews")
    parser.add_argument("--summary-only", action="store_true", help="Publish text immediately without reading images")
    parser.add_argument("--embed-images", action="store_true", help="Embed PNGs for a portable single file; default uses lazy local images")
    parser.add_argument("--plan-images", action="store_true", help="List unchanged reusable cards and cards requiring generation, without generating images")
    args = parser.parse_args()
    directory = args.directory.resolve()
    if args.plan_images:
        copy = json.loads((directory / 'copy.json').read_text())
        path = directory / 'generated-sources.json'
        sources = json.loads(path.read_text()) if path.exists() else {'cards': []}
        images = {item['id']: item for item in sources['cards']}
        plan = []
        for card in copy['cards']:
            entry = images.get(card['id'])
            reusable = bool(entry and entry.get('image_key') == image_key(card) and Path(entry['path']).is_file())
            plan.append({'id': card['id'], 'action': 'reuse' if reusable else 'generate', 'image_key': image_key(card)})
        print(json.dumps({'cards': plan, 'generate_count': sum(item['action']=='generate' for item in plan)}, ensure_ascii=False))
        return
    digest_path = directory / "digest.json"
    digest = json.loads(digest_path.read_text()) if digest_path.exists() else None
    digest_html, digest_markdown = render_digest(digest) if digest else ("", "")
    if digest:
        summary_page = '<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + html.escape(digest['title']) + '</title><style>body{max-width:900px;margin:32px auto;padding:0 20px;background:#f5f8fc;color:#10223c;font-family:system-ui,"Apple SD Gothic Neo",sans-serif;line-height:1.75}.digest-item{background:white;border:1px solid #dce5ef;border-radius:12px;padding:16px 22px;margin:12px 0}.note{font-size:14px;color:#52617a}a{color:#133c78}h3{margin-top:0}</style>' + digest_html.rsplit('<h2>이미지 카드뉴스</h2>', 1)[0] + '</section></html>'
        (directory / "summary.html").write_text(summary_page)
        (directory / "summary.md").write_text(digest_markdown)
    if args.summary_only:
        if not digest:
            parser.error("--summary-only requires digest.json")
        print(json.dumps({"summary": str(directory / 'summary.html'), "image_reads": 0, "elapsed_ms": round((perf_counter()-started)*1000, 2)}))
        return
    copy = json.loads((directory / "copy.json").read_text())
    sources = json.loads((directory / "generated-sources.json").read_text())
    images = {item["id"]: item for item in sources["cards"]}
    if len(images) != len(sources["cards"]) or set(images) != {card["id"] for card in copy["cards"]}:
        raise ValueError("Each copy card must have exactly one generated image")
    prepared = []
    for index, card in enumerate(copy["cards"], 1):
        if images[card['id']].get('image_key') != image_key(card):
            raise ValueError(f"Image content changed or unbound: {card['id']}; use --plan-images and regenerate the changed card")
        source = Path(images[card["id"]]["path"])
        content = source.read_bytes()
        if content[:8] != b"\x89PNG\r\n\x1a\n" or content[12:16] != b"IHDR":
            raise ValueError(f"Not a PNG: {source}")
        width, height = struct.unpack(">II", content[16:24])
        prepared.append((index, card, source, content, width, height))
    cards = []
    manifest = []
    escape = html.escape
    for index, card, source, content, width, height in prepared:
        filename = f"{index:02d}-{card['id']}.png"
        destination = directory / filename
        if source.resolve() != destination.resolve() and (not destination.exists() or hashlib.sha256(destination.read_bytes()).digest() != hashlib.sha256(content).digest()):
            shutil.copyfile(source, destination)
        manifest.append({"id": card["id"], "file": filename, "width": width, "height": height,
                         "sha256": hashlib.sha256(content).hexdigest(), "badge": card["badge"]})
        bullets = "".join(f"<li>{escape(item)}</li>" for item in card["bullets"])
        links = " · ".join(f'<a href="{escape(item["url"], quote=True)}" rel="noreferrer">{escape(item["label"])}</a>' for item in card["sources"])
        if not links:
            links = "지정 대화방의 수집 텍스트 · 공개 링크 없음"
        image_src = 'data:image/png;base64,' + base64.b64encode(content).decode("ascii") if args.embed_images else filename
        cards.append(f'''<article><details open><summary><img src="{image_src}" loading="lazy" decoding="async" alt="{escape(card['headline'], quote=True)}" width="{width}" height="{height}"><h2>{index:02d}. {escape(card['topic'])}</h2><p>{escape(card['badge'])} · {escape(card['short_limit'])}</p></summary><div class="body"><p>{escape(card['summary'])}</p><ul>{bullets}</ul><p>{escape(card['why'])}</p><p class="note">{escape(card['caution'])}</p><p>{links}</p></div></details></article>''')
    page = '''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>REPORT_TITLE</title><style>
    *{box-sizing:border-box}body{margin:0;background:#edf3f8;color:#071936;font-family:system-ui,"Apple SD Gothic Neo",sans-serif}header,main,footer{max-width:1280px;margin:auto;padding:24px}header h1{font-size:clamp(26px,5vw,44px);margin:12px 0}header p{line-height:1.7;max-width:760px}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,340px),1fr));gap:24px}article{background:white;border-radius:16px;overflow:hidden;box-shadow:0 3px 20px #10203a0d}summary{cursor:pointer;list-style:none}summary::-webkit-details-marker{display:none}img{display:block;width:100%;height:auto}h2,summary p{margin:16px 20px}h2{font-size:19px}summary p,.note{font-size:14px;color:#52617a}.body{padding:0 20px 20px;line-height:1.7}.body ul{padding-left:20px}a{color:#133c78}footer{font-size:13px;line-height:1.8;color:#52617a}@media print{main{display:block}article{page-break-after:always;box-shadow:none}header,footer,.body,h2,summary p{display:none}img{max-height:96vh;object-fit:contain}}</style><header><p>BLACK SHEEP WALL · KAKAO DIGEST</p><h1>REPORT_TITLE</h1><p>REPORT_METADATA</p></header><main>''' + "".join(cards) + '''</main><footer>그림은 개념 도해입니다. 대화에 첨부된 이미지·음성·영상은 해석하지 않았습니다. 공식 페이지 확인은 직접 제품 실행이나 성능 검증을 뜻하지 않습니다. 참여자의 이름·프로필·원문 대화는 포함하지 않았습니다. 이 파일은 이미지와 스타일이 내장되어 오프라인에서도 열립니다.</footer></html>'''
    if digest:
        page = page.replace("<main>", digest_html + "<main>", 1)
        page = page.replace("</style>", ".digest{max-width:1280px;margin:auto;padding:0 24px}.digest>ul{line-height:1.8;padding-left:24px}.digest h2{margin:24px 0 16px}.digest-item{background:white;border:1px solid #dce5ef;border-radius:12px;padding:18px 22px;margin:12px 0;line-height:1.75}.digest-item h3{margin:0 0 10px;font-size:19px}.digest-item p{margin:8px 0}.digest-item strong{color:#a53216}@media print{.digest{display:none}}</style>", 1)
        (directory / "summary.md").write_text(digest_markdown)
    title = digest['title'] if digest else '대화 요약과 카드뉴스'
    metadata = (digest['collected_date'] + ' · ' + digest['room'] + ' · ') if digest else ''
    page = page.replace('REPORT_TITLE', escape(title)).replace('REPORT_METADATA', escape(metadata + '카드 ' + str(len(cards)) + '장 · 근거와 확인 범위는 각 카드 아래에 표시합니다.'))
    if not args.embed_images:
        page = page.replace('이 파일은 이미지와 스타일이 내장되어 오프라인에서도 열립니다.', '이미지는 같은 폴더의 PNG를 필요할 때 불러옵니다. 폴더째 보관하면 오프라인에서도 열립니다.')
    (directory / "index.html").write_text(page)
    (directory / "images.json").write_text(json.dumps({"format": "cardnews-images-v1", "backend": sources["backend"], "cards": manifest}, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"gallery": str(directory / "index.html"), "embedded": args.embed_images, "html_bytes": len(page.encode()), "elapsed_ms": round((perf_counter()-started)*1000, 2), "cards": manifest}, ensure_ascii=False))


if __name__ == "__main__":
    main()
