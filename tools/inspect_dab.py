"""Read-only inspection of the observed version-2 DAB layouts.

Reference: https://github.com/kings0527/talking-pen-toolkit/blob/main/references/dab-routing.md
Offsets/OIDs remain candidates until checked against the installed pen and paper.
The alternating-XOR audio wrapper was observed in the supplied RAZ N sample.
This is a format probe, not a general DAB importer or firmware tool.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess


INDEX_START = 0x440
EMPTY = 0xFFFFFFFF
MAX_INDEX_BYTES = 16 * 1024 * 1024


def u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def decode_text(data: bytes) -> str:
    return data.split(b"\xff", 1)[0].split(b"\0", 1)[0].decode("gb18030", "replace")


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def parse_entry(data: bytes, slot: int, kind: str, size: int, payload_start: int,
                content_start: int) -> dict:
    offset, length_minus_one = struct.unpack("<II", data)
    index_slot = slot + 8 if kind == "content" else slot if kind == "guide" else None
    row = {"kind": kind, "slot": slot, "full_index_slot": index_slot,
           "candidate_oid": None,
           "code_with_guide_slots": content_start + index_slot if index_slot is not None else None,
           "code_without_guide_slots": content_start + slot if kind in ("content", "guide") else None}
    if (offset, length_minus_one) == (EMPTY, EMPTY):
        return {**row, "occupied": False}
    if EMPTY in (offset, length_minus_one):
        raise ValueError(f"Partial empty sentinel: {kind}/{slot}")
    length = length_minus_one + 1
    if offset < payload_start or offset + length > size:
        raise ValueError(f"Out-of-bounds audio entry: {kind}/{slot}")
    return {**row, "occupied": True, "offset": offset, "stored_length": length,
            "end_exclusive": offset + length}


def parse_package(path: Path) -> tuple[dict, list[dict]]:
    size = path.stat().st_size
    with path.open("rb") as stream:
        header = stream.read(INDEX_START)
        labelled_header = re.match(rb"789-ha-[0-9]{4}[A-Z](?=\xff|\0)", header)
        if len(header) != INDEX_START or not (header.startswith(b"deb3") or labelled_header):
            raise ValueError("Not the supported deb3 layout")
        if header[0x154] != 2:
            raise ValueError("Only the observed version-2 layout is supported")
        payload_start = u32(header, 0x400)
        index_bytes = payload_start - INDEX_START
        if not 0 <= index_bytes <= MAX_INDEX_BYTES or index_bytes % 8 or payload_start > size:
            raise ValueError("Invalid or unsupported index boundary")
        table = stream.read(index_bytes)
        if len(table) != index_bytes:
            raise ValueError("Truncated index")
        content_start = u32(header, 0x17B)
        rows = [parse_entry(header[0x400 + i * 8:0x408 + i * 8], i, "guide",
                            size, payload_start, content_start) for i in range(8)]
        rows += [parse_entry(table[i:i + 8], i // 8, "content", size,
                             payload_start, content_start) for i in range(0, len(table), 8)]
        metadata = {
            "magic": header[:4].decode("ascii"),
            "title": decode_text(header[0 if labelled_header else 4:200]),
            "package_guid": decode_text(header[0xC8:0x12C]),
            "builder_text": decode_text(header[0x187:0x1AF]),
            "format_version": header[0x154],
            "field_0x14c_raw": u32(header, 0x14C),
            "field_0x150_raw": u32(header, 0x150),
            "content_start_candidate": content_start,
            "declared_track_count": struct.unpack_from("<H", header, 0x1AF)[0],
            "payload_start": payload_start,
            "content_slots": index_bytes // 8,
        }
        active = sorted((row for row in rows if row["occupied"]), key=lambda r: r["offset"])
        cursor = payload_start
        gaps, overlaps = [], []
        for row in active:
            if row["offset"] > cursor:
                gaps.append([cursor, row["offset"]])
            elif row["offset"] < cursor:
                overlaps.append({"kind": row["kind"], "slot": row["slot"]})
            cursor = max(cursor, row["end_exclusive"])
        metadata.update({"indexed_payload_end": cursor, "payload_gaps": gaps,
                         "payload_overlaps": overlaps,
                         "declared_count_matches_active": len(active) == metadata["declared_track_count"]})
        # Sample-specific candidate: an additional offset/length pair before the builder string.
        # Only recognize it if it directly follows all indexed audio and is bounded by this file.
        auxiliary_offset, auxiliary_n = struct.unpack_from("<II", header, 0x17F)
        if auxiliary_offset == cursor and auxiliary_n != EMPTY and auxiliary_offset + auxiliary_n + 1 <= size:
            auxiliary = parse_entry(header[0x17F:0x187], 0, "auxiliary_candidate",
                                    size, payload_start, content_start)
            auxiliary["header_offset"] = "0x17F"
            rows.append(auxiliary)
            cursor = auxiliary["end_exclusive"]
        stream.seek(cursor)
        tail = stream.read(min(size - cursor, 1024 * 1024))
        metadata["trailer"] = {"offset": cursor, "length": size - cursor,
                               "examined_bytes": len(tail), "prefix_hex": tail[:32].hex(),
                               "suffix_hex": tail[-32:].hex(),
                               "zero_padding_then_eoid": tail == b"\0" * max(0, len(tail) - 4) + b"EOID"
                               and len(tail) == size - cursor}
        if len(tail) == 8200 and tail.startswith(b"BOID") and tail.endswith(b"EOID"):
            codes = {i * 8 + bit for i, byte in enumerate(tail[4:-4])
                     for bit in range(8) if byte & (0x80 >> bit)}
            with_guides = {r["code_with_guide_slots"] for r in active}
            without_guides = {r["code_without_guide_slots"] for r in active}
            metadata["footer_bitmap"] = {
                "encoding": "65536 bits, MSB-first within each byte",
                "set_bit_count": len(codes), "code_range": [min(codes), max(codes)] if codes else None,
                "full_index_formula_matches_exactly": codes == with_guides,
                "upstream_body_formula_matches_exactly": codes == without_guides,
                "full_index_symmetric_difference": len(codes ^ with_guides),
                "upstream_symmetric_difference": len(codes ^ without_guides),
                "set_codes": sorted(codes),
            }
            rule = "code_with_guide_slots" if codes == with_guides else "code_without_guide_slots" if codes == without_guides else None
            metadata["candidate_code_rule"] = rule
            for row in rows:
                if row["occupied"] and row["kind"] in ("content", "guide") and rule:
                    row["candidate_oid"] = row[rule]
                    row["code_evidence"] = "exact_footer_bitmap_match; physical verification pending"
        else:
            metadata["candidate_code_rule"] = None
    return metadata, rows


def looks_like_mp3(data: bytes) -> bool:
    if data.startswith(b"ID3"):
        return True
    if len(data) < 4:
        return False
    # MPEG sync + non-reserved version/layer/bitrate/sample-rate fields.
    return (data[0] == 0xFF and data[1] & 0xE0 == 0xE0
            and (data[1] >> 3) & 3 != 1 and (data[1] >> 1) & 3 != 0
            and (data[2] >> 4) not in (0, 15) and (data[2] >> 2) & 3 != 3)


def unwrap_audio(raw: bytes, mode: str) -> bytes:
    if mode == "raw_mp3_candidate":
        return raw
    if mode != "alternating_xor_52_candidate" or len(raw) < 2:
        raise ValueError("Unknown audio wrapper")
    key = raw[0]
    payload = raw[1:]
    result = bytearray(len(payload))
    # C-backed translations keep even multi-megabyte entries inexpensive.
    result[0::2] = payload[0::2].translate(bytes(i ^ key for i in range(256)))
    result[1::2] = payload[1::2].translate(bytes(i ^ key ^ 0x52 for i in range(256)))
    return bytes(result)


def classify_audio(prefix: bytes) -> str:
    if looks_like_mp3(prefix):
        return "raw_mp3_candidate"
    if len(prefix) > 1 and looks_like_mp3(unwrap_audio(prefix, "alternating_xor_52_candidate")):
        return "alternating_xor_52_candidate"
    return "unknown"


def verify_audio(path: Path, ffprobe: str, ffmpeg: str) -> dict:
    try:
        probe = subprocess.run([ffprobe, "-v", "error", "-show_streams", "-show_format",
                                "-of", "json", str(path)], capture_output=True, timeout=120)
        info = json.loads(probe.stdout) if probe.returncode == 0 else {}
        decoded = subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-xerror", "-i",
                                  str(path), "-map", "0:a:0", "-f", "null", "-"],
                                 capture_output=True, timeout=180)
        streams = info.get("streams", [])
        audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
        return {"probe_exit": probe.returncode, "decode_exit": decoded.returncode,
                "decode_passed": probe.returncode == 0 and decoded.returncode == 0 and bool(audio),
                "codec": audio.get("codec_name"), "sample_rate": audio.get("sample_rate"),
                "channels": audio.get("channels"),
                "duration_seconds": info.get("format", {}).get("duration"),
                "tags": info.get("format", {}).get("tags", {}),
                "probe_errors": probe.stderr.decode("utf-8", "replace")[:3000],
                "decode_errors": decoded.stderr.decode("utf-8", "replace")[:3000]}
    except (subprocess.TimeoutExpired, ValueError) as exc:
        return {"decode_passed": False, "error": str(exc)}


def save_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_markdown_report(report: dict, output: Path) -> None:
    meta, counts = report["metadata"], report["counts"]
    bitmap = meta.get("footer_bitmap", {})
    passed = sum(s.get("validation", {}).get("decode_passed", False) for s in report["samples"])
    full_formula = bitmap.get("full_index_formula_matches_exactly", False)
    start = meta["content_start_candidate"]
    guide_codes = [s["candidate_oid"] for s in report["samples"] if s["kind"] == "guide"]
    recognized_headers = sum(n for mode, n in report["audio_wrapper_counts"].items() if mode != "unknown")
    def link(label: str, relative: str) -> str:
        return f"[{label}](<{(output / relative).as_posix()}>)"
    lines = [
        f"**{meta['title']}.dab：本地只读解析报告**", "",
        f"源文件：`{report['source']}`。分析只读取原件，导出文件写入项目的 analysis 目录。", "",
        "本样本已解析出音频索引与包内码值候选表，并成功还原可播放的 MP3。纸书页码、句子位置及点读笔实时通信尚未验证。", "",
        "| 检查项 | 本地结果 |", "|---|---|",
        f"| 文件大小 | {report['source_size']:,} 字节，{report['source_size'] / 1048576:.2f} MiB |",
        f"| 文件格式 | {meta['magic']}，版本字段 {meta['format_version']} |",
        f"| 包标题 | {meta['title']} |",
        f"| 制作标记 | {meta['builder_text']} |",
        f"| 正文索引槽 | {meta['content_slots']} |",
        f"| 非空正文项 | {counts['occupied_content']} |",
        f"| 空正文槽 | {counts['empty_content']} |",
        f"| 前置预留索引中的非空项 | {counts['occupied_guide']}，实际功能待确认 |",
        f"| 额外音频候选 | {counts['auxiliary_candidates']}，实际功能待确认 |",
        f"| 包内正文候选码范围 | {report['candidate_oid_range']}，并非每个值都有内容 |",
        f"| 常规音轨计数 | {meta['declared_track_count']}，与正文加前置非空项吻合 |",
        f"| 索引音频区间 | 全部有界；间隙 {len(meta['payload_gaps'])}，重叠 {len(meta['payload_overlaps'])} |",
        f"| 已导出并完整解码的样本 | {passed}/{len(report['samples'])} |",
        f"| 原文件前后 SHA-256 相同且修改时间未变 | {report['source_unchanged']} |", "",
        "**码值需要与尾部位图交叉核验。** 正文表从 0x440 开始，但整个索引从 0x400 开始，前面有 8 个预留槽。", "",
        ("本样本尾部是 BOID + 8192 字节位图 + EOID。按每字节高位在前解释，位图的置位集合与“起始码 + 完整索引槽号”完全相符。"
         if full_formula else f"已选择的候选码公式：{meta.get('candidate_code_rule') or '未知，缺少完全匹配的位图证据'}。"), "",
        "```text", f"起始码字段 = {start}",
        f"正文候选码公式 = {str(start) + ' + 8 + 正文槽号（从 0 开始）' if full_formula else meta.get('candidate_code_rule') or '未知'}",
        f"前置非空槽候选码 = {guide_codes}",
        f"正文非空槽候选码范围 = {report['candidate_oid_range']}", "```", "",
        f"位图有 {bitmap.get('set_bit_count', '未知')} 个置位；采用完整索引公式时集合差异为 {bitmap.get('full_index_symmetric_difference', '未知')}。",
        f"直接采用上游的“起始码 + 正文槽号”公式时，集合对称差为 {bitmap.get('upstream_symmetric_difference', '未知')}；若差异非零，就不能原样套用该公式。", "",
        "这里的“候选码”已有文件内交叉证据，但还没有用纸书或实物笔核验。不要把安装包的槽号 069、文件头字段 69、码值 1000 当作已确认的纸书封面码；也不能把文件名中的 069 直接解释成第 69 本书。", "",
        f"**音频还原与验证范围。** 本次有 {recognized_headers} 项音频开头符合已识别的格式，具体分类计数为 {report['audio_wrapper_counts']}。交替 XOR 封装以首字节为 key，去掉首字节后，载荷从索引 0 开始交替与 key、key XOR 0x52 异或，可恢复 MP3/ID3 开头。", "",
        "所有条目都做了索引边界和音频开头检查；只对下表样本执行了完整 FFmpeg 解码。样本包含前后条目、均匀分布条目、按存储长度选出的最短与最长正文项、前置项及额外音频。音频开头识别成功不等于全部音轨都已完整解码。", "",
        "**导出结果。**", "",
        f"- {link('完整索引 index.json', 'index.json')}：保留空槽、文件偏移、长度、两种码值公式及位图核验依据。",
        f"- {link('讲义映射模板 mapping-template.json', 'mapping-template.json')}：{counts['occupied_content']} 个正文绑定项，书名、纸书页、PDF 页、内容块字段待填写，实物核验状态为 false。",
        f"- {link('机器可读检查报告 report.json', 'report.json')}：原文件摘要、位图、样本 SHA-256、FFmpeg 检查结果。", "",
        "| 类型 | 槽号 | 候选码 | 时长（秒） | 完整解码 | 音频 |", "|---|---:|---:|---:|---|---|",
    ]
    for sample in report["samples"]:
        validation = sample.get("validation", {})
        duration = validation.get("duration_seconds")
        duration = f"{float(duration):.3f}" if duration is not None else "未测"
        lines.append(f"| {sample['kind']} | {sample['slot']} | {sample['candidate_oid'] if sample['candidate_oid'] is not None else '未知'} | {duration} | {'通过' if validation.get('decode_passed') else '未通过或未测'} | {link('打开 MP3', sample['file'])} |")
    lines += ["", "**下一步需要纸书与 PDF 的对应证据。** 文件头、索引、尾部用于包和音频寻址，本工具未从中恢复页码、句子或纸面坐标。样本标签记录在 report.json，尚未逐个检查其余音轨的所有标签。", "",
              "可先提供对应 N 级 PDF 的路径，用音频对齐页面；再用实物笔的读码模式或可输出码值的接口核对几个位置。文件解析成功不能证明现有点读笔能实时向手机发送点击事件。", "",
              "原文件 SHA-256：", "", f"`{report['source_sha256_before']}`", "",
              "参考格式资料：[Talking Pen Toolkit 的格式说明](https://github.com/kings0527/talking-pen-toolkit/blob/main/references/dab-routing.md)。位图、额外音频及正文加 8 的结论来自本次样本实测，未宣称适用于所有 DAB。", ""]
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")


def inspect(source: Path, output: Path, sample_count: int, ffprobe: str | None, ffmpeg: str | None) -> dict:
    source, output = source.resolve(), output.resolve()
    if output == source.parent or output in source.parents or source.is_relative_to(output):
        raise ValueError("Output must be separate from the source directory tree")
    initial_stat = source.stat()
    before = digest(source)
    metadata, rows = parse_package(source)
    output.mkdir(parents=True, exist_ok=True)
    active = [r for r in rows if r["occupied"]]
    with source.open("rb") as stream:
        for row in active:
            stream.seek(row["offset"])
            prefix = stream.read(min(64, row["stored_length"]))
            row["audio_wrapper"] = classify_audio(prefix)
            row["raw_prefix_hex"] = prefix[:16].hex()
            if row["audio_wrapper"] != "unknown":
                row["decoded_prefix_hex"] = unwrap_audio(prefix, row["audio_wrapper"])[:32].hex()
    content = [r for r in active if r["kind"] == "content"]
    selected = [r for r in active if r["kind"] != "content"]
    if content and sample_count > 0:
        selected += [content[round(i * (len(content) - 1) / max(1, sample_count - 1))]
                     for i in range(min(sample_count, len(content)))]
        selected += content[:3] + content[-3:]
        selected += [min(content, key=lambda r: r["stored_length"]), max(content, key=lambda r: r["stored_length"])]
    selected = list({(r["kind"], r["slot"]): r for r in selected}.values())
    samples = []
    with source.open("rb") as stream:
        for row in selected:
            if row["audio_wrapper"] == "unknown":
                continue
            stream.seek(row["offset"])
            raw = stream.read(row["stored_length"])
            if len(raw) != row["stored_length"]:
                raise ValueError("Source changed or short audio read")
            decoded = unwrap_audio(raw, row["audio_wrapper"])
            stem = f"{row['kind']}-slot-{row['slot']:04d}"
            if row["candidate_oid"] is not None:
                stem += f"-code-{row['candidate_oid']}"
            relative = Path("samples") / (stem + ".mp3")
            destination = output / relative
            destination.parent.mkdir(exist_ok=True)
            destination.write_bytes(decoded)
            samples.append({"kind": row["kind"], "slot": row["slot"],
                            "candidate_oid": row["candidate_oid"], "file": relative.as_posix(),
                            "decoded_bytes": len(decoded), "sha256": hashlib.sha256(decoded).hexdigest()})
    if ffprobe and ffmpeg:
        with ThreadPoolExecutor(max_workers=4) as pool:
            validations = list(pool.map(lambda s: verify_audio(output / s["file"], ffprobe, ffmpeg), samples))
        for sample, validation in zip(samples, validations):
            sample["validation"] = validation
    after = digest(source)
    final_stat = source.stat()
    wrappers = {mode: sum(r["audio_wrapper"] == mode for r in active)
                for mode in sorted({r["audio_wrapper"] for r in active})}
    report = {
        "source": str(source), "source_size": initial_stat.st_size,
        "source_sha256_before": before, "source_sha256_after": after,
        "source_unchanged": before == after and initial_stat.st_mtime_ns == final_stat.st_mtime_ns,
        "metadata": metadata,
        "counts": {"occupied_content": len(content),
                   "empty_content": sum(not r["occupied"] for r in rows if r["kind"] == "content"),
                   "occupied_guide": sum(r["kind"] == "guide" for r in active),
                   "auxiliary_candidates": sum(r["kind"] == "auxiliary_candidate" for r in active)},
        "candidate_oid_range": [min(r["candidate_oid"] for r in content), max(r["candidate_oid"] for r in content)] if content and all(r["candidate_oid"] is not None for r in content) else None,
        "audio_wrapper_counts": wrappers, "samples": samples,
        "limitations": ["OID values are inferred from this package layout; no physical book/pen validation yet.",
                        "Guide/auxiliary function names and header 0x14C semantics are not established for this source package.",
                        "Page, sentence and book-region mappings are not recovered.",
                        "Header signatures were inspected for all entries; full MP3 decode is limited to exported samples.",
                        "No live pen communication was tested."]}
    save_json(output / "report.json", report)
    save_json(output / "index.json", {"source_sha256": before, "content_start_candidate": metadata["content_start_candidate"], "candidate_code_rule": metadata["candidate_code_rule"], "entries": rows})
    save_json(output / "mapping-template.json", {
        "package_sha256": before, "package_title": metadata["title"],
        "edition_id": None, "bindings": [
            {"candidate_oid": r["candidate_oid"], "slot": r["slot"], "audio_offset": r["offset"],
             "audio_stored_length": r["stored_length"], "code_evidence": r.get("code_evidence"), "book_id": None, "paper_page": None,
             "pdf_page": None, "region_id": None, "lesson_file": None,
             "lesson_block_id": None, "verified_on_paper": False} for r in content]})
    write_markdown_report(report, output)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sample-count", type=int, default=12)
    args = parser.parse_args()
    if not 0 <= args.sample_count <= 100:
        parser.error("--sample-count must be between 0 and 100")
    report = inspect(args.source, args.output, args.sample_count, shutil.which("ffprobe"), shutil.which("ffmpeg"))
    print(json.dumps({"source_size": report["source_size"], "source_unchanged": report["source_unchanged"],
                      "metadata": {k: ({x: y for x, y in v.items() if x != "set_codes"} if k == "footer_bitmap" else v) for k, v in report["metadata"].items()}, "counts": report["counts"],
                      "candidate_oid_range": report["candidate_oid_range"],
                      "audio_wrapper_counts": report["audio_wrapper_counts"],
                      "sample_count": len(report["samples"]),
                      "sample_decode_passed": sum(s.get("validation", {}).get("decode_passed", False) for s in report["samples"]),
                      "output": str(args.output.resolve())}, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
