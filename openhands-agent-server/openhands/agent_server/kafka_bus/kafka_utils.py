import base64
import zlib


msg_body_max_size = 1024 * 768  # 768KB 消息体最大大小; 单位是 byte


def compression(value: str | None) -> str:
    """
    将传入的字符串进行压缩
    Argos:
    value: 需要压缩的字符串

    Returns:
        base64 编码的 zlib 压缩结果（空串原样返回）
    """
    if not value:
        return ""

    # level=6：默认级别，压缩比与速度较均衡，适合日志等文本
    compressed = zlib.compress(value.encode("utf-8"), level=6)
    return base64.b64encode(compressed).decode("ascii")


def decompression(value: str | None) -> str:
    """
    解压 compression 生成的字符串
    Argos:
    value: base64 编码的 zlib 压缩结果

    Returns:
        原始字符串（空串原样返回）
    """
    if not value:
        return ""

    compressed = base64.b64decode(value.encode("ascii"))
    return zlib.decompress(compressed).decode("utf-8")


if __name__ == "__main__":
    pass
