#!/usr/bin/env python3
"""
Extract Mandarin coach comments from training videos and build a training log.

Requirements:
  - ffmpeg installed and available in PATH
  - pip install faster-whisper
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Sequence


# General coaching cue words (simplified + traditional). Used to flag a
# transcript line as a likely coach instruction.
COACH_CUE_KEYWORDS = [
    # 通用提示
    "注意", "不要", "别", "應該", "应该", "要", "记住", "記住",
    "问题", "問題", "错误", "錯誤", "改", "再来", "再來",
    "太快", "太慢", "不对", "不對", "不够", "不夠",
    "稳定", "穩定", "放松", "放鬆", "紧张", "緊張",
    "提前", "保持", "控制",
    # 动作要点
    "重心", "中心", "发力", "發力", "动作", "動作", "节奏", "節奏",
    "角度", "路径", "路徑", "轨迹", "軌跡",
    "抬", "压", "壓", "转", "轉", "拉", "推",
    "蹬", "伸", "送", "锁", "鎖", "顶", "頂", "沉", "收", "翻",
    # 身体部位 / 关节
    "髋", "髖", "膝", "踝", "肩", "腕", "肘", "腰",
    "脚掌", "腳掌", "脚后跟", "腳後跟", "脚尖", "腳尖",
    "膝盖", "膝蓋", "髋部", "髖部", "腰背", "腹",
    "手臂", "手肘",
    # 举重专用术语
    "杠铃", "槓鈴", "抓举", "抓舉", "挺举", "挺舉",
    "高翻", "前蹲", "后蹲", "後蹲", "深蹲", "硬拉",
    "翻铃", "翻鈴", "提铃", "提鈴", "接铃", "接鈴",
    "支撑", "支撐", "锁定", "鎖定", "起动", "啟動", "启动",
    "预备", "預備", "姿势", "姿勢",
    "蹬伸", "髋膝", "髖膝", "三关节", "三關節",
    "下蹲", "上挺", "借力", "发力点", "發力點",
    "出杠", "上拉", "下拉", "二次发力", "二次發力",
]


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class TermCorrection:
    time: str
    wrong: str
    correct: str
    reason: str
    confidence: float
    source: str = "ai"  # "auto" for deterministic dictionary, "ai" for Ollama review


# Deterministic Mandarin Whisper mishearings frequently observed in
# Olympic-weightlifting / strength-training coaching audio.
#
# Each entry: (wrong_pattern, correct_text, reason).
# - Patterns are intentionally multi-character (>= 2 hanzi) so we don't
#   over-correct on a single ambiguous character.
# - Both Simplified and Traditional Chinese variants are listed, because
#   faster-whisper can output either depending on the audio.
# - Order does not strictly matter (we sort by length descending at runtime),
#   but related entries are grouped for readability.
WEIGHTLIFTING_CORRECTIONS: List[tuple[str, str, str]] = [
    # === 预备姿势 / 預備姿勢 (setup position) ===
    ("约备自示", "预备姿势", "预备姿势同音误识"),
    ("预备自示", "预备姿势", "姿势同音误识"),
    ("训练自示", "预备姿势", "预备姿势同音误识"),
    ("预备资式", "预备姿势", "姿势同音误识"),
    ("预备姿式", "预备姿势", "姿势同音误识"),
    ("预备子式", "预备姿势", "姿势同音误识"),
    ("約備自示", "預備姿勢", "預備姿勢同音誤識"),
    ("預備自示", "預備姿勢", "姿勢同音誤識"),
    ("訓練自示", "預備姿勢", "預備姿勢同音誤識"),
    ("預備資式", "預備姿勢", "姿勢同音誤識"),

    # === 杠铃 / 槓鈴 (barbell) ===
    ("钢灵", "杠铃", "杠铃同音误识"),
    ("钢领", "杠铃", "杠铃同音误识"),
    ("钢令", "杠铃", "杠铃同音误识"),
    ("刚铃", "杠铃", "杠铃同音误识"),
    ("刚灵", "杠铃", "杠铃同音误识"),
    ("鋼靈", "槓鈴", "槓鈴同音誤識"),
    ("鋼領", "槓鈴", "槓鈴同音誤識"),
    ("鋼令", "槓鈴", "槓鈴同音誤識"),
    ("剛鈴", "槓鈴", "槓鈴同音誤識"),
    ("剛靈", "槓鈴", "槓鈴同音誤識"),

    # === 翻铃 / 翻鈴 (catch / turnover) ===
    ("翻灵", "翻铃", "翻铃同音误识"),
    ("翻零", "翻铃", "翻铃/翻零同音误识"),
    ("翻令", "翻铃", "翻铃同音误识"),
    ("翻靈", "翻鈴", "翻鈴同音誤識"),

    # === 提铃 / 接铃 ===
    ("提灵", "提铃", "提铃同音误识"),
    ("提零", "提铃", "提铃同音误识"),
    ("接灵", "接铃", "接铃同音误识"),
    ("接零", "接铃", "接铃同音误识"),
    ("提靈", "提鈴", "提鈴同音誤識"),
    ("接靈", "接鈴", "接鈴同音誤識"),

    # === 重心 vs 中心 (center of gravity) — only correct in load-context phrases ===
    ("中心彩", "重心踩", "重心+踩同音误识"),
    ("中心踩", "重心踩", "重心同音误识"),
    ("中心往", "重心往", "重心同音误识"),
    ("中心在", "重心在", "重心同音误识"),
    ("中心偏", "重心偏", "重心同音误识"),
    ("中心要", "重心要", "重心同音误识"),
    ("中心是", "重心是", "重心同音误识"),
    ("中心都", "重心都", "重心同音误识"),
    ("中心不变", "重心不变", "重心同音误识"),
    ("中心不變", "重心不變", "重心同音誤識"),
    ("中心后移", "重心后移", "重心同音误识"),
    ("中心後移", "重心後移", "重心同音誤識"),
    ("中心前移", "重心前移", "重心同音误识"),
    ("中心问题", "重心问题", "重心同音误识"),
    ("中心問題", "重心問題", "重心同音誤識"),
    ("中心后", "重心后", "重心同音误识"),
    ("中心後", "重心後", "重心同音誤識"),
    ("中心慢", "重心慢", "重心同音误识"),
    ("启动的手并重心", "启动的手臂重心", "手臂同音误识"),
    ("起動的手並重心", "起動的手臂重心", "手臂同音誤識"),
    ("下端中心", "下蹲重心", "下蹲+重心同音误识"),

    # === 踩 (彩→踩) ===
    ("彩在", "踩在", "踩同音误识"),
    ("彩到", "踩到", "踩同音误识"),
    ("脚彩", "脚踩", "踩同音误识"),
    ("腳彩", "腳踩", "踩同音誤識"),
    ("彩着", "踩着", "踩同音误识"),
    ("彩著", "踩著", "踩同音誤識"),

    # === 手臂 (手并→手臂) ===
    ("手并", "手臂", "手臂同音误识"),
    ("手並", "手臂", "手臂同音誤識"),
    ("手必", "手臂", "手臂同音误识"),
    ("手庇", "手臂", "手臂同音误识"),

    # === 髋 / 髖 (hip) ===
    ("右宽", "右髋", "髋同音误识"),
    ("左宽", "左髋", "髋同音误识"),
    ("右寬", "右髖", "髖同音誤識"),
    ("左寬", "左髖", "髖同音誤識"),
    ("宽部", "髋部", "髋同音误识"),
    ("寬部", "髖部", "髖同音誤識"),
    ("宽关节", "髋关节", "髋同音误识"),
    ("寬關節", "髖關節", "髖同音誤識"),
    ("送宽", "送髋", "髋同音误识"),
    ("送寬", "送髖", "髖同音誤識"),
    ("展宽", "展髋", "髋同音误识"),
    ("展寬", "展髖", "髖同音誤識"),
    ("顶宽", "顶髋", "髋同音误识"),
    ("頂寬", "頂髖", "髖同音誤識"),
    ("摆宽", "摆髋", "髋同音误识"),
    ("擺寬", "擺髖", "髖同音誤識"),
    ("收宽", "收髋", "髋同音误识"),
    ("收寬", "收髖", "髖同音誤識"),

    # === 前 (钱→前 / 錢→前) ===
    ("偏钱", "偏前", "前同音误识"),
    ("偏錢", "偏前", "前同音誤識"),
    ("旋钱", "旋前", "前同音误识"),
    ("旋錢", "旋前", "前同音誤識"),
    ("向钱", "向前", "前同音误识"),
    ("向錢", "向前", "前同音誤識"),
    ("往钱", "往前", "前同音误识"),
    ("往錢", "往前", "前同音誤識"),
    ("靠钱", "靠前", "前同音误识"),
    ("靠錢", "靠前", "前同音誤識"),

    # === 高翻 (高端→高翻 in coaching context only) ===
    ("高端要注意", "高翻要注意", "高翻同音误识"),
    ("高端的时候", "高翻的时候", "高翻同音误识"),
    ("高端的時候", "高翻的時候", "高翻同音誤識"),
    ("高端动作", "高翻动作", "高翻同音误识"),
    ("高端動作", "高翻動作", "高翻同音誤識"),
    ("做高端", "做高翻", "高翻同音误识"),
    ("练高端", "练高翻", "高翻同音误识"),
    ("練高端", "練高翻", "高翻同音誤識"),
    ("高端发力", "高翻发力", "高翻同音误识"),
    ("高端發力", "高翻發力", "高翻同音誤識"),

    # === 蹬伸 / 蹬地 ===
    ("登展", "蹬伸", "蹬伸同音误识"),
    ("登伸", "蹬伸", "蹬伸同音误识"),
    ("等底", "蹬地", "蹬地同音误识"),
    ("等地", "蹬地", "蹬地同音误识"),
    ("推低", "蹬地", "蹬地同音误识"),
    ("推底", "蹬地", "蹬地同音误识"),
    ("推抵", "蹬地", "蹬地同音误识"),
    ("登地", "蹬地", "蹬地同音误识"),

    # === 起动 / 啟動 — keep 起动 as the preferred weightlifting term ===
    ("啟动", "起动", "繁简混用"),
    ("啟動", "起動", "繁体一致化"),
    ("啓动", "起动", "繁简混用"),

    # === 硬拉 (动拉→硬拉) ===
    ("动拉", "硬拉", "硬拉同音误识"),
    ("動拉", "硬拉", "硬拉同音誤識"),
    ("硬腊", "硬拉", "拉同音误识"),

    # === 抓举 / 挺举 ===
    ("抓军", "抓举", "举同音误识"),
    ("抓軍", "抓舉", "舉同音誤識"),
    ("抓巨", "抓举", "举同音误识"),
    ("挺军", "挺举", "举同音误识"),
    ("挺軍", "挺舉", "舉同音誤識"),
    ("挺穷胎的", "挺举的", "挺举的连读误识"),
    ("挺窮胎的", "挺舉的", "挺舉的連讀誤識"),
    ("挺穷胎", "挺举", "挺举连读误识"),
    ("挺窮胎", "挺舉", "挺舉連讀誤識"),

    # === 后跟 / 後跟 ===
    ("脚后根", "脚后跟", "后跟同音误识"),
    ("腳後根", "腳後跟", "後跟同音誤識"),
    ("脚后讚", "脚后跟", "后跟同音误识"),
    ("脚厚跟", "脚后跟", "后跟同音误识"),

    # === 正后方 / 正後方 ===
    ("正互方", "正后方", "后方同音误识"),
    ("正胡方", "正后方", "后方同音误识"),
    ("正护方", "正后方", "后方同音误识"),

    # === 吃力 (尺力→吃力) ===
    ("尺力", "吃力", "吃力同音误识"),

    # === 举重课 / 舉重課 ===
    ("举中课", "举重课", "举重同音误识"),
    ("舉中課", "舉重課", "舉重同音誤識"),
    ("巨中课", "举重课", "举重同音误识"),

    # === 伸蹬 (身端→伸蹬) — coaching context ===
    ("身端就慢", "伸蹬就慢", "伸蹬同音误识"),
    ("身端慢", "伸蹬慢", "伸蹬同音误识"),
    ("身端就快", "伸蹬就快", "伸蹬同音误识"),
    ("身端快", "伸蹬快", "伸蹬同音误识"),
    ("身端不够", "伸蹬不够", "伸蹬同音误识"),
    ("身端不夠", "伸蹬不夠", "伸蹬同音誤識"),

    # === 锁定 ===
    ("锁腚", "锁定", "锁定同音误识"),
    ("鎖腚", "鎖定", "鎖定同音誤識"),
    ("锁顶", "锁定", "锁定同音误识"),
    ("鎖頂", "鎖定", "鎖定同音誤識"),

    # === 收腹 ===
    ("收复", "收腹", "收腹同音误识"),
    ("收府", "收腹", "收腹同音误识"),
    ("收覆", "收腹", "收腹同音误识"),
    ("收輔", "收腹", "收腹同音誤識"),

    # === 沉肩 / 顶肩 ===
    ("沉间", "沉肩", "沉肩同音误识"),
    ("沉箭", "沉肩", "沉肩同音误识"),
    ("顶间", "顶肩", "顶肩同音误识"),
    ("頂間", "頂肩", "頂肩同音誤識"),

    # === 杠铃路径 ===
    ("钢灵路径", "杠铃路径", "杠铃同音误识"),
    ("钢铃路径", "杠铃路径", "杠铃同音误识"),

    # === 脚趾翘起 (脚指头一跳→脚趾头一翘) ===
    ("脚指头一跳", "脚趾头一翘", "脚趾翘起误识"),
    ("腳指頭一跳", "腳趾頭一翹", "腳趾翹起誤識"),
    ("脚指头一调", "脚趾头一翘", "脚趾翘起误识"),
]


def apply_dictionary_corrections(
    segments: Sequence["Segment"],
) -> tuple[List["Segment"], List["TermCorrection"]]:
    """Apply deterministic high-confidence dictionary corrections.

    Returns the corrected segments plus a list of TermCorrection records
    (one per unique replacement that fired) so they can be surfaced in the
    term-review markdown for transparency.
    """
    # Sort patterns longest-first to avoid clobbering longer multi-hanzi
    # phrases by shorter overlapping ones (e.g. handle "下端中心" before "中心在").
    rules = sorted(
        WEIGHTLIFTING_CORRECTIONS,
        key=lambda item: len(item[0]),
        reverse=True,
    )

    # Track the time-of-first-occurrence for each rule that actually fires.
    fired: dict[tuple[str, str], dict] = {}

    corrected: List[Segment] = []
    for seg in segments:
        new_text = seg.text
        for wrong, correct, reason in rules:
            if wrong == correct or not wrong:
                continue
            if wrong in new_text:
                count = new_text.count(wrong)
                new_text = new_text.replace(wrong, correct)
                key = (wrong, correct)
                entry = fired.get(key)
                if entry is None:
                    fired[key] = {
                        "time": to_hms(seg.start),
                        "reason": reason,
                        "count": count,
                    }
                else:
                    entry["count"] += count
        corrected.append(Segment(start=seg.start, end=seg.end, text=new_text))

    corrections: List[TermCorrection] = []
    for (wrong, correct), meta in fired.items():
        corrections.append(
            TermCorrection(
                time=meta["time"],
                wrong=wrong,
                correct=correct,
                reason=f"{meta['reason']} (×{meta['count']})",
                confidence=0.99,
                source="auto",
            )
        )
    return corrected, corrections


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract Mandarin coach feedback from MP4 files into a training log."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("videos"),
        help="Directory containing MP4 files (default: ./videos).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("training_log_output"),
        help="Directory for generated logs (default: ./training_log_output).",
    )
    parser.add_argument(
        "--glob",
        type=str,
        default="*.MP4",
        help="Filename pattern for videos (default: *.MP4).",
    )
    parser.add_argument(
        "--glob2",
        type=str,
        default="*.mp4",
        help="Secondary filename pattern for videos (default: *.mp4).",
    )
    parser.add_argument(
        "--model-size",
        type=str,
        default="small",
        choices=["tiny", "base", "small", "medium", "large-v2", "large-v3"],
        help="Whisper model size for transcription (default: small).",
    )
    parser.add_argument(
        "--compute-type",
        type=str,
        default="int8",
        help="faster-whisper compute_type (default: int8).",
    )
    parser.add_argument(
        "--device",
        type=str,
        default="auto",
        help="faster-whisper device, e.g. auto/cpu/cuda (default: auto).",
    )
    parser.add_argument(
        "--language",
        type=str,
        default="zh",
        help="Language code passed to Whisper (default: zh).",
    )
    parser.add_argument(
        "--min-feedback-chars",
        type=int,
        default=4,
        help="Minimum text length to keep a feedback segment (default: 4).",
    )
    parser.add_argument(
        "--feedback-mode",
        type=str,
        default="hybrid",
        choices=["keyword", "hybrid", "all"],
        help=(
            "How to pick comments: keyword=only keyword hits, "
            "all=all segments, hybrid=keyword hits else fallback to all (default: hybrid)."
        ),
    )
    parser.add_argument(
        "--summary-model",
        type=str,
        default="qwen2.5:7b-instruct",
        help="Ollama model for summarization (default: qwen2.5:7b-instruct).",
    )
    parser.add_argument(
        "--disable-summary",
        action="store_true",
        help="Disable Ollama summarization step.",
    )
    parser.add_argument(
        "--term-review-model",
        type=str,
        default="qwen2.5:7b-instruct",
        help="Ollama model for transcript wrong-word review (default: qwen2.5:7b-instruct).",
    )
    parser.add_argument(
        "--term-review-context",
        type=str,
        default="奥林匹克举重（抓举、挺举、高翻、前蹲、硬拉、发力、重心、髋膝踝伸展）",
        help="Domain context sent to Ollama when reviewing potential wrong words.",
    )
    parser.add_argument(
        "--disable-term-review",
        action="store_true",
        help="Disable Ollama potential wrong-word review step.",
    )
    parser.add_argument(
        "--auto-correct-transcript",
        action="store_true",
        help=(
            "Auto-correct transcript text using high-confidence Ollama term "
            "corrections, and save raw transcript backup."
        ),
    )
    parser.add_argument(
        "--min-correction-confidence",
        type=float,
        default=0.75,
        help="Minimum confidence for auto-correction (0.0-1.0, default: 0.75).",
    )
    parser.add_argument(
        "--disable-dictionary-corrections",
        action="store_true",
        help=(
            "Disable the deterministic weightlifting term dictionary corrections "
            "(by default they ARE applied to clean up common Whisper mishearings)."
        ),
    )
    parser.add_argument(
        "--summary-timeout-seconds",
        type=int,
        default=420,
        help="Timeout for summary Ollama call (default: 420).",
    )
    parser.add_argument(
        "--term-review-timeout-seconds",
        type=int,
        default=420,
        help="Timeout for term-review Ollama call (default: 420).",
    )
    parser.add_argument(
        "--term-review-max-chars",
        type=int,
        default=10000,
        help="Max transcript chars sent to term-review Ollama (default: 10000).",
    )
    return parser.parse_args()


def ensure_ffmpeg() -> None:
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    except Exception as exc:
        raise RuntimeError(
            "ffmpeg is required but not found. Install ffmpeg and retry."
        ) from exc


def collect_videos(input_dir: Path, patterns: Sequence[str]) -> List[Path]:
    seen = set()
    videos: List[Path] = []
    for pattern in patterns:
        for path in sorted(input_dir.glob(pattern)):
            resolved = path.resolve()
            if resolved not in seen and path.is_file():
                seen.add(resolved)
                videos.append(path)
    return videos


def extract_audio(video_path: Path, wav_path: Path) -> None:
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-ac",
        "1",
        "-ar",
        "16000",
        "-vn",
        str(wav_path),
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)


def transcribe_audio(
    wav_path: Path, model, language: str
) -> List[Segment]:
    segments, _info = model.transcribe(
        str(wav_path),
        language=language,
        vad_filter=True,
        beam_size=5,
        condition_on_previous_text=True,
    )
    out: List[Segment] = []
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        out.append(Segment(start=float(seg.start), end=float(seg.end), text=text))
    return out


def is_feedback(text: str, min_chars: int) -> bool:
    compact = text.strip().replace(" ", "")
    if len(compact) < min_chars:
        return False
    return any(keyword in compact for keyword in COACH_CUE_KEYWORDS)


def select_feedback_segments(
    segments: Sequence[Segment], min_chars: int, mode: str
) -> List[Segment]:
    if mode == "all":
        return [
            seg
            for seg in segments
            if len(seg.text.strip().replace(" ", "")) >= min_chars
        ]

    keyword_hits = [seg for seg in segments if is_feedback(seg.text, min_chars)]
    if mode == "keyword":
        return keyword_hits

    # hybrid mode: avoid empty results when keyword matching misses coach phrases.
    if keyword_hits:
        return keyword_hits
    return [
        seg
        for seg in segments
        if len(seg.text.strip().replace(" ", "")) >= min_chars
    ]


def to_hms(seconds: float) -> str:
    whole = max(0, int(seconds))
    h = whole // 3600
    m = (whole % 3600) // 60
    s = whole % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def format_segments(segments: Sequence[Segment]) -> str:
    lines = []
    for seg in segments:
        lines.append(f"[{to_hms(seg.start)} - {to_hms(seg.end)}] {seg.text}")
    return "\n".join(lines)


def strip_think_blocks(text: str) -> str:
    # Some local models emit <think>...</think>; hide it from final logs.
    while "<think>" in text and "</think>" in text:
        start = text.find("<think>")
        end = text.find("</think>", start)
        if end == -1:
            break
        text = text[:start] + text[end + len("</think>") :]
    # Remove terminal color/control sequences that may appear in streamed output.
    text = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", text)

    # Drop "Thinking..." preamble if model emits chain-of-thought style traces.
    lowered = text.lower()
    start = lowered.find("thinking...")
    end = lowered.find("...done thinking.")
    if start != -1 and end != -1 and end > start:
        text = text[end + len("...done thinking.") :]

    lines = []
    for line in text.splitlines():
        stripped = line.strip().lower()
        if stripped in {"thinking...", "...done thinking."}:
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def extract_first_json_array(text: str) -> str:
    stripped = strip_think_blocks(text).strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        parts = stripped.split("\n", 1)
        if len(parts) == 2:
            stripped = parts[1]
    start = stripped.find("[")
    end = stripped.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise RuntimeError("Ollama output did not contain JSON array.")
    return stripped[start : end + 1]


def parse_json_array_resilient(raw_text: str):
    array_text = extract_first_json_array(raw_text)
    attempts = [
        array_text,
        re.sub(r"[\x00-\x1f]", " ", array_text),
        re.sub(r",\s*([}\]])", r"\1", re.sub(r"[\x00-\x1f]", " ", array_text)),
    ]
    last_error: Exception | None = None
    for candidate in attempts:
        try:
            return json.loads(candidate)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    raise RuntimeError(f"Failed to parse Ollama JSON array: {last_error}")


def run_ollama(model_name: str, prompt: str, timeout_seconds: int) -> str:
    result = subprocess.run(
        ["ollama", "run", model_name],
        input=prompt,
        capture_output=True,
        text=True,
        check=True,
        timeout=max(60, timeout_seconds),
    )
    return result.stdout


def summarize_feedback_with_ollama(
    video_name: str,
    feedback_segments: Sequence[Segment],
    all_segments: Sequence[Segment],
    model_name: str,
    timeout_seconds: int,
) -> str:
    feedback_block = format_segments(feedback_segments) if feedback_segments else "(none)"
    transcript_block = format_segments(all_segments)

    # Keep prompt bounded for very long videos.
    transcript_block = transcript_block[:12000]
    feedback_block = feedback_block[:8000]

    prompt = f"""
你是训练日志助手。请基于下面的视频转写内容，输出中文总结，聚焦“教练指出的问题”和“可执行改进动作”。

要求：
1) 不要复述全部转写，只提炼关键点。
2) 用以下结构输出，严格保持标题：
## 关键问题
- ...

## 改进建议
- ...

## 下次训练检查点
- ...

3) 每一条尽量具体，包含动作细节（例如重心、节奏、发力顺序、左右侧差异）。
4) 如果有时间点，写在括号中，如（00:01:32）。
5) 只输出最终答案，不要输出思考过程，不要输出<think>标签。

视频：{video_name}

候选反馈片段：
{feedback_block}

完整转写（可能截断）：
{transcript_block}
""".strip()

    summary = strip_think_blocks(run_ollama(model_name, prompt, timeout_seconds))
    if not summary:
        raise RuntimeError("Ollama returned empty summary.")
    return summary


def review_transcript_terms_with_ollama(
    video_name: str,
    all_segments: Sequence[Segment],
    model_name: str,
    context: str,
    timeout_seconds: int,
    max_chars: int,
) -> List[TermCorrection]:
    transcript_block = format_segments(all_segments)
    transcript_block = transcript_block[: max(2000, max_chars)]

    prompt = f"""
你是转写质检助手。请检查下面的中文语音转写，场景是：{context}。

目标：
- 识别“可能的错词/错字/术语误识别”。
- 只列出你有一定把握的可疑项；不确定就不要编造。

输出要求（严格）：
1) 仅输出 JSON 数组，不要输出任何其他文本，不要 markdown。
2) 数组元素结构必须是：
   {{
     "time": "00:01:23",
     "wrong": "原转写片段",
     "correct": "建议词或短句",
     "reason": "简短原因",
     "confidence": 0.0-1.0
   }}
3) 只保留你把握较高的候选项；没有就返回 []。
4) 术语优先参考举重语境（如抓举、挺举、重心、发力、髋、膝、踝、杠铃路径等）。
5) 不要输出思考过程，不要输出<think>标签。

视频：{video_name}

转写文本（可能截断）：
{transcript_block}
""".strip()

    try:
        output = run_ollama(model_name, prompt, timeout_seconds)
    except subprocess.TimeoutExpired:
        # Retry once with shorter context to improve reliability.
        shorter = transcript_block[: max(1500, max_chars // 2)]
        retry_prompt = prompt.replace(transcript_block, shorter)
        output = run_ollama(model_name, retry_prompt, timeout_seconds)
    parsed = parse_json_array_resilient(output)
    corrections: List[TermCorrection] = []
    if not isinstance(parsed, list):
        raise RuntimeError("Ollama term review JSON was not a list.")
    for item in parsed:
        if not isinstance(item, dict):
            continue
        wrong = str(item.get("wrong", "")).strip()
        correct = str(item.get("correct", "")).strip()
        if not wrong or not correct or wrong == correct:
            continue
        reason = str(item.get("reason", "")).strip() or "术语上下文不一致"
        time_str = str(item.get("time", "")).strip() or "-"
        try:
            confidence = float(item.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        corrections.append(
            TermCorrection(
                time=time_str,
                wrong=wrong,
                correct=correct,
                reason=reason,
                confidence=confidence,
            )
        )
    return corrections


def build_term_review_markdown(corrections: Sequence[TermCorrection]) -> str:
    auto_items = [c for c in corrections if c.source == "auto"]
    ai_items = [c for c in corrections if c.source != "auto"]

    lines: List[str] = []

    lines.append("## 词典自动修正（已应用）")
    lines.append("| 时间点 | 原转写片段 | 修正为 | 原因 | 置信度 |")
    lines.append("|---|---|---|---|---|")
    if not auto_items:
        lines.append("| - | - | - | 词典未触发任何修正 | - |")
    else:
        for item in sorted(auto_items, key=lambda c: c.time):
            lines.append(
                f"| {item.time} | {item.wrong} | {item.correct} | "
                f"{item.reason} | {item.confidence:.2f} |"
            )

    lines.append("")
    lines.append("## AI 模型候选错词（需人工确认）")
    lines.append("| 时间点 | 原转写片段 | 建议词/句 | 原因 | 置信度 |")
    lines.append("|---|---|---|---|---|")
    if not ai_items:
        lines.append("| - | - | - | 模型未发现高把握候选 | - |")
    else:
        for item in ai_items:
            lines.append(
                f"| {item.time} | {item.wrong} | {item.correct} | "
                f"{item.reason} | {item.confidence:.2f} |"
            )

    lines.extend(
        [
            "",
            "## 说明",
            "- 第一节是基于举重领域固定术语的确定性修正，已直接应用到 transcript.txt。",
            "- 第二节是 AI 候选清单，仅供参考，需要人工确认；如启用 --auto-correct-transcript 且置信度达阈值，会同样应用。",
            "- 原始未修改的转写保存在 *.transcript.raw.txt。",
        ]
    )
    return "\n".join(lines)


def apply_transcript_corrections(
    segments: Sequence[Segment],
    corrections: Sequence[TermCorrection],
    min_confidence: float,
) -> tuple[List[Segment], int]:
    eligible = [
        c
        for c in corrections
        if c.confidence >= min_confidence and len(c.wrong.strip()) >= 2
    ]
    eligible.sort(key=lambda c: len(c.wrong), reverse=True)

    corrected_segments: List[Segment] = []
    applied_count = 0
    for seg in segments:
        new_text = seg.text
        for c in eligible:
            if c.wrong in new_text:
                replacements = new_text.count(c.wrong)
                if replacements > 0:
                    new_text = new_text.replace(c.wrong, c.correct)
                    applied_count += replacements
        corrected_segments.append(Segment(start=seg.start, end=seg.end, text=new_text))
    return corrected_segments, applied_count


def write_video_outputs(
    out_dir: Path,
    video_name: str,
    transcript_segments: Sequence[Segment],
    raw_segments: Sequence[Segment] | None = None,
    summary_text: str | None = None,
    term_review_text: str | None = None,
) -> None:
    video_stem = Path(video_name).stem
    transcript_path = out_dir / f"{video_stem}.transcript.txt"
    raw_transcript_path = out_dir / f"{video_stem}.transcript.raw.txt"
    summary_path = out_dir / f"{video_stem}.summary.md"
    term_review_path = out_dir / f"{video_stem}.term_review.md"

    with transcript_path.open("w", encoding="utf-8") as f:
        for seg in transcript_segments:
            f.write(f"[{to_hms(seg.start)} - {to_hms(seg.end)}] {seg.text}\n")

    if raw_segments is not None:
        with raw_transcript_path.open("w", encoding="utf-8") as f:
            for seg in raw_segments:
                f.write(f"[{to_hms(seg.start)} - {to_hms(seg.end)}] {seg.text}\n")

    if summary_text is not None:
        with summary_path.open("w", encoding="utf-8") as f:
            f.write(summary_text.strip() + "\n")

    if term_review_text is not None:
        with term_review_path.open("w", encoding="utf-8") as f:
            f.write(term_review_text.strip() + "\n")


def write_global_training_log(
    out_dir: Path,
    all_feedback_rows: Sequence[dict],
    model_size: str,
    input_dir: Path,
) -> None:
    md_path = out_dir / "training_log.md"
    json_path = out_dir / "training_log.json"

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    grouped: dict[str, List[dict]] = {}
    for row in all_feedback_rows:
        grouped.setdefault(row["video"], []).append(row)

    with md_path.open("w", encoding="utf-8") as f:
        f.write("# Training Log (Coach Feedback)\n\n")
        f.write(f"- Generated: {now}\n")
        f.write(f"- Input dir: {input_dir.resolve()}\n")
        f.write(f"- Transcription model: faster-whisper `{model_size}`\n")
        f.write(
            "- Note: feedback detection is keyword-based, so review manually for accuracy.\n\n"
        )
        for video in sorted(grouped.keys()):
            f.write(f"## {video}\n\n")
            for row in grouped[video]:
                f.write(f"- [{row['start']}] {row['text']}\n")
            f.write("\n")

    with json_path.open("w", encoding="utf-8") as f:
        json.dump(all_feedback_rows, f, ensure_ascii=False, indent=2)


def load_whisper_model(model_size: str, device: str, compute_type: str):
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency `faster-whisper`.\n"
            "Install with: pip install faster-whisper"
        ) from exc
    return WhisperModel(model_size, device=device, compute_type=compute_type)


def main() -> int:
    args = parse_args()
    input_dir = args.input_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    ensure_ffmpeg()
    model = load_whisper_model(args.model_size, args.device, args.compute_type)

    videos = collect_videos(input_dir, [args.glob, args.glob2])
    if not videos:
        print("No videos found. Adjust --input-dir / --glob and retry.")
        return 1

    print(f"Found {len(videos)} videos.")
    all_feedback_rows = []

    with tempfile.TemporaryDirectory(prefix="coach_audio_") as tmp:
        tmp_dir = Path(tmp)
        for idx, video in enumerate(videos, start=1):
            print(f"[{idx}/{len(videos)}] Processing {video.name} ...")
            wav_path = tmp_dir / f"{video.stem}.wav"
            extract_audio(video, wav_path)

            segments = transcribe_audio(wav_path, model, args.language)

            # --- Pass 1: deterministic dictionary corrections (always on by default) ---
            dict_corrections: List[TermCorrection] = []
            if args.disable_dictionary_corrections:
                dict_corrected_segments = list(segments)
            else:
                dict_corrected_segments, dict_corrections = apply_dictionary_corrections(
                    segments
                )
                if dict_corrections:
                    print(
                        f"  dictionary-correct applied unique_terms={len(dict_corrections)} "
                        f"total_replacements={sum(int(c.reason.split('×')[-1].rstrip(')')) for c in dict_corrections if '×' in c.reason)}"
                    )

            # --- Pass 2: Ollama term review on the dictionary-cleaned text ---
            ai_corrections: List[TermCorrection] = []
            term_review_failed_reason: str | None = None
            if not args.disable_term_review or args.auto_correct_transcript:
                try:
                    ai_corrections = review_transcript_terms_with_ollama(
                        video_name=video.name,
                        all_segments=dict_corrected_segments,
                        model_name=args.term_review_model,
                        context=args.term_review_context,
                        timeout_seconds=args.term_review_timeout_seconds,
                        max_chars=args.term_review_max_chars,
                    )
                except Exception as exc:
                    term_review_failed_reason = str(exc)
                    print(f"  warning: term review generation failed: {exc}")

            # --- Pass 3: optionally apply Ollama corrections on top of dict pass ---
            transcript_segments = dict_corrected_segments
            ai_applied_count = 0
            if args.auto_correct_transcript and ai_corrections:
                transcript_segments, ai_applied_count = apply_transcript_corrections(
                    segments=dict_corrected_segments,
                    corrections=ai_corrections,
                    min_confidence=max(0.0, min(1.0, args.min_correction_confidence)),
                )
                print(f"  ai-correct applied replacements={ai_applied_count}")
            elif args.auto_correct_transcript:
                print("  ai-correct applied replacements=0")

            # Save raw backup whenever any correction (dict OR ai) actually changed text.
            any_corrections_applied = (
                bool(dict_corrections) or ai_applied_count > 0
            )
            raw_segments: Sequence[Segment] | None = (
                segments if any_corrections_applied else None
            )

            # Build term review markdown combining both correction sources.
            term_review_text: str | None = None
            if not args.disable_term_review:
                if term_review_failed_reason and not dict_corrections and not ai_corrections:
                    term_review_text = (
                        "## 词典自动修正（已应用）\n"
                        "| 时间点 | 原转写片段 | 修正为 | 原因 | 置信度 |\n"
                        "|---|---|---|---|---|\n"
                        "| - | - | - | 词典未触发任何修正 | - |\n\n"
                        "## AI 模型候选错词（需人工确认）\n"
                        "| 时间点 | 原转写片段 | 建议词/句 | 原因 | 置信度 |\n"
                        "|---|---|---|---|---|\n"
                        f"| - | - | - | 质检生成失败: {term_review_failed_reason} | - |\n\n"
                        "## 说明\n"
                        "- 这是候选清单，需要人工确认。"
                    )
                else:
                    term_review_text = build_term_review_markdown(
                        list(dict_corrections) + list(ai_corrections)
                    )

            feedback_segments = select_feedback_segments(
                transcript_segments,
                min_chars=args.min_feedback_chars,
                mode=args.feedback_mode,
            )

            summary_text: str | None = None
            if not args.disable_summary:
                try:
                    summary_text = summarize_feedback_with_ollama(
                        video_name=video.name,
                        feedback_segments=feedback_segments,
                        all_segments=transcript_segments,
                        model_name=args.summary_model,
                        timeout_seconds=args.summary_timeout_seconds,
                    )
                except Exception as exc:
                    summary_text = (
                        "## 关键问题\n"
                        "- 摘要生成失败，请检查 Ollama 服务和模型是否可用。\n\n"
                        "## 改进建议\n"
                        f"- 失败原因: {exc}\n\n"
                        "## 下次训练检查点\n"
                        "- 先参考反馈列表与完整转写手动整理。"
                    )
                    print(f"  warning: summary generation failed: {exc}")

            write_video_outputs(
                output_dir,
                video.name,
                transcript_segments=transcript_segments,
                raw_segments=raw_segments,
                summary_text=summary_text,
                term_review_text=term_review_text,
            )

            for seg in feedback_segments:
                all_feedback_rows.append(
                    {
                        "video": video.name,
                        "start": to_hms(seg.start),
                        "end": to_hms(seg.end),
                        "text": seg.text,
                    }
                )

            print(
                f"  segments={len(segments)}, feedback={len(feedback_segments)} "
                f"(saved in {output_dir})"
            )

    write_global_training_log(output_dir, all_feedback_rows, args.model_size, input_dir)
    print(f"\nDone. Training log written to: {output_dir / 'training_log.md'}")
    print(f"Structured JSON written to: {output_dir / 'training_log.json'}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(f"Command failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
