"""群文件：把 NapCat（go-cqhttp 兼容）群文件接口的返回整理成控制台用的结构。

只在打开控制台「文件」标签时实时读取，不落盘；下载链接由 QQ 临时签发，不保存。
"""
import re
from urllib.parse import parse_qsl, quote, urlsplit, urlunsplit

FILE_LIMIT = 500  # 单个目录一次最多列出的文件数


def _int(value, default=0):
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return default


def _text(value, limit=300):
    return str(value or "").replace("\n", " ").replace("\r", " ").strip()[:limit]


def _data(resp):
    return resp.get("data", resp) if isinstance(resp, dict) else None


def listing(resp):
    """get_group_root_files / get_group_files_by_folder 的返回 → {folders, files}。"""
    data = _data(resp)
    if not isinstance(data, dict):
        raise ValueError("群文件接口返回格式无效")
    folders = []
    for f in data.get("folders") or []:
        if not isinstance(f, dict) or not f.get("folder_id"):
            continue
        folders.append({"folder_id": _text(f.get("folder_id")), "name": _text(f.get("folder_name") or f.get("folder")) or "未命名文件夹",
                        "count": _int(f.get("total_file_count")), "create_time": _int(f.get("create_time")),
                        "creator_name": _text(f.get("creator_name"), 80)})
    files = []
    for f in data.get("files") or []:
        if not isinstance(f, dict) or not f.get("file_id"):
            continue
        files.append({"file_id": _text(f.get("file_id")), "busid": _int(f.get("busid")),
                      "name": _text(f.get("file_name")) or "未命名文件", "size": _int(f.get("size", f.get("file_size"))),
                      "upload_time": _int(f.get("upload_time")), "dead_time": _int(f.get("dead_time")),
                      "download_times": _int(f.get("download_times")),
                      "uploader": _text(f.get("uploader"), 20), "uploader_name": _text(f.get("uploader_name"), 80)})
    folders.sort(key=lambda x: x["name"])
    files.sort(key=lambda x: -x["upload_time"])
    return {"folders": folders, "files": files[:FILE_LIMIT], "truncated": len(files) >= FILE_LIMIT}


def usage(resp):
    """get_group_file_system_info 的返回 → 文件数与空间用量；格式不对时返回 None。"""
    data = _data(resp)
    if not isinstance(data, dict):
        return None
    return {"file_count": _int(data.get("file_count")), "limit_count": _int(data.get("limit_count")),
            "used_space": _int(data.get("used_space")), "total_space": _int(data.get("total_space"))}


def file_url(resp):
    """get_group_file_url 的返回 → 下载地址；只接受 http(s)，其他一律视为没有。"""
    data = _data(resp)
    url = data.get("url") if isinstance(data, dict) else None
    return url.strip() if isinstance(url, str) and re.match(r"^https?://", url.strip(), re.I) else ""


def with_name(url, name):
    """QQ 的下载链接带的是空的 fname=，响应头只有 Content-Disposition: attachment，浏览器会存成无扩展名的文件；
    把 fname 填上原文件名后 QQ 会在响应头里返回文件名。fname 已有值时不动。"""
    name = re.sub(r"[\x00-\x1f\x7f/\\]", "", str(name or "")).strip()[:255]
    if not url or not name:
        return url
    parts = urlsplit(url)
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    if any(k == "fname" and v for k, v in pairs):
        return url
    # 只去掉空的 fname=，其余参数原样保留（不重新编码，避免改坏签名）
    query = "&".join(x for x in parts.query.split("&") if x and x.split("=", 1)[0] != "fname")
    query = (query + "&" if query else "") + "fname=" + quote(name, safe="")
    return urlunsplit(parts._replace(query=query))


def error_reason(exc):
    """NapCat 报错的可读原因，去掉其中可能带签名的链接。"""
    message = ""
    for attr in ("result", "info"):
        info = getattr(exc, attr, None)
        if isinstance(info, dict):
            message = info.get("wording") or info.get("message") or info.get("msg") or ""
            if message:
                break
    message = re.sub(r"https?://\S+", "[链接]", str(message or exc)).strip()
    return message[:120] or type(exc).__name__
