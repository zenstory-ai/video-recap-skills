from lib import CONFIG
from lib import log, run_cmd

# ── Step 1: 帧提取 ───────────────────────────────────────────────────

# ffmpeg 的 `fps` 滤镜从源时间 0 开始输出第一帧，而 image2 muxer 的 `-start_number`
# 默认是 1。所以 frame_00001.jpg 对应 t=0，第 n 号帧对应 (n-1)/fps —— 不是 n/fps。
# 这个 off-by-one 会把所有画面锚点（frame_facts、场景归属、storyboard 标签）整体推后
# 1/fps 秒（长视频默认 fps=1，即整整 1 秒）。这里是该约定的唯一定义处。
FRAME_START_NUMBER = 1

# 帧编号↔时间的换算规则变更时递增；进入各阶段缓存 key，强制重算旧产物。
FRAME_TIME_CONVENTION_VERSION = 2


def frame_time(frame_number, fps):
    """帧文件序号 → 源视频时间（秒）。frame_00001 → 0.0。"""
    fps = float(fps)
    if fps <= 0:
        raise ValueError("fps 必须大于 0")
    return (int(frame_number) - FRAME_START_NUMBER) / fps


def frame_number_for_time(timestamp, fps):
    """源视频时间（秒）→ 对应的帧文件序号（可能是小数，供最近邻查找用）。"""
    fps = float(fps)
    if fps <= 0:
        raise ValueError("fps 必须大于 0")
    return float(timestamp) * fps + FRAME_START_NUMBER


def parse_frame_number(frame_path):
    """从 frame_NNNNN.jpg 解析序号；命名不符合约定时返回 None。"""
    parts = frame_path.stem.split("_")
    if len(parts) != 2 or not parts[1].isdigit():
        return None
    return int(parts[1])


def extract_frames(video_path, work_dir, fps=None):
    """提取视频帧"""
    fps = CONFIG["fps"] if fps is None else fps
    if fps <= 0:
        raise ValueError("fps 必须大于 0；完整 pipeline 会自动计算 fps")
    frames_dir = work_dir / "frames"
    frames_dir.mkdir(exist_ok=True)

    # 清理上一次（可能更高 fps）残留的帧，避免陈旧帧泄漏进本次结果
    for stale in frames_dir.glob("frame_*.jpg"):
        stale.unlink()

    output_pattern = str(frames_dir / "frame_%05d.jpg")
    cmd = ["ffmpeg", "-y", "-i", str(video_path),
           "-vf", f"fps={fps}", "-q:v", "2",
           "-start_number", str(FRAME_START_NUMBER), output_pattern]
    result = run_cmd(cmd)
    if result.returncode != 0:
        raise RuntimeError(f"帧提取失败: {result.stderr}")

    frames = sorted(frames_dir.glob("frame_*.jpg"))
    log(f"提取了 {len(frames)} 帧 ({fps}fps)")
    return frames
