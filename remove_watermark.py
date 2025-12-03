import cv2
import numpy as np
import os
from typing import Tuple, Optional

def remove_watermark_inpainting(image_path: str, output_path: Optional[str] = None, 
                               watermark_region: Optional[Tuple[int, int, int, int]] = None,
                               method: str = 'telea') -> bool:
    """
    使用图像修复技术去除水印
    
    Args:
        image_path (str): 输入图片路径
        output_path (str, optional): 输出图片路径，如果为None则自动生成
        watermark_region (tuple, optional): 水印区域坐标 (x, y, width, height)
        method (str): 修复方法，'telea' 或 'ns'，默认为 'telea'
    
    Returns:
        bool: 成功返回True，失败返回False
    """
    
    # 检查图片文件是否存在
    if not os.path.exists(image_path):
        print(f"错误: 图片文件 '{image_path}' 不存在")
        return False
    
    # 读取图片
    image = cv2.imread(image_path)
    if image is None:
        print(f"错误: 无法读取图片文件 '{image_path}'")
        return False
    
    # 如果没有指定水印区域，尝试自动检测（简单实现）
    if watermark_region is None:
        # 这里可以添加自动检测水印的逻辑
        # 暂时使用图片右下角作为默认区域
        height, width = image.shape[:2]
        watermark_region = (width - 200, height - 100, 200, 100)
        print(f"使用默认水印区域: {watermark_region}")
    
    x, y, w, h = watermark_region
    
    # 确保区域在图片范围内
    x = max(0, min(x, image.shape[1] - 1))
    y = max(0, min(y, image.shape[0] - 1))
    w = max(1, min(w, image.shape[1] - x))
    h = max(1, min(h, image.shape[0] - y))
    
    # 创建掩码（标记需要修复的区域）
    mask = np.zeros(image.shape[:2], dtype=np.uint8)
    mask[y:y+h, x:x+w] = 255
    
    # 选择修复方法
    if method == 'telea':
        inpaint_method = cv2.INPAINT_TELEA
    elif method == 'ns':
        inpaint_method = cv2.INPAINT_NS
    else:
        print(f"警告: 未知的修复方法 '{method}'，使用默认方法 'telea'")
        inpaint_method = cv2.INPAINT_TELEA
    
    # 执行图像修复
    result = cv2.inpaint(image, mask, 3, inpaint_method)
    
    # 生成输出路径
    if output_path is None:
        image_name = os.path.splitext(os.path.basename(image_path))[0]
        output_path = f"{image_name}_no_watermark.jpg"
    
    # 确保输出目录存在
    output_dir = os.path.dirname(output_path)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir)
    
    # 保存结果
    success = cv2.imwrite(output_path, result)
    
    if success:
        print(f"成功去除水印，保存到: {output_path}")
        print(f"原图尺寸: {image.shape[1]}x{image.shape[0]}")
        print(f"修复区域: ({x}, {y}) 到 ({x+w}, {y+h})")
    else:
        print(f"错误: 无法保存图片到 '{output_path}'")
    
    return success

def remove_watermark_clone(image_path: str, output_path: Optional[str] = None,
                          source_region: Tuple[int, int, int, int] = None,
                          target_region: Tuple[int, int, int, int] = None) -> bool:
    """
    使用图像克隆技术去除水印
    
    Args:
        image_path (str): 输入图片路径
        output_path (str, optional): 输出图片路径
        source_region (tuple): 源区域坐标 (x, y, width, height)
        target_region (tuple): 目标区域坐标 (x, y, width, height)
    
    Returns:
        bool: 成功返回True，失败返回False
    """
    
    if not os.path.exists(image_path):
        print(f"错误: 图片文件 '{image_path}' 不存在")
        return False
    
    image = cv2.imread(image_path)
    if image is None:
        print(f"错误: 无法读取图片文件 '{image_path}'")
        return False
    
    # 如果没有指定区域，使用默认值
    if source_region is None or target_region is None:
        height, width = image.shape[:2]
        # 默认使用图片左上角区域克隆到右下角水印区域
        source_region = (50, 50, 100, 100)
        target_region = (width - 150, height - 80, 100, 100)
        print(f"使用默认克隆区域: 源{source_region} -> 目标{target_region}")
    
    src_x, src_y, src_w, src_h = source_region
    dst_x, dst_y, dst_w, dst_h = target_region
    
    # 提取源区域
    source_patch = image[src_y:src_y+src_h, src_x:src_x+src_w]
    
    # 创建掩码
    mask = 255 * np.ones(source_patch.shape, source_patch.dtype)
    
    # 执行克隆
    center = (dst_x + dst_w // 2, dst_y + dst_h // 2)
    result = cv2.seamlessClone(source_patch, image, mask, center, cv2.NORMAL_CLONE)
    
    # 生成输出路径
    if output_path is None:
        image_name = os.path.splitext(os.path.basename(image_path))[0]
        output_path = f"{image_name}_cloned.jpg"
    
    # 确保输出目录存在
    output_dir = os.path.dirname(output_path)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir)
    
    # 保存结果
    success = cv2.imwrite(output_path, result)
    
    if success:
        print(f"成功使用克隆技术去除水印，保存到: {output_path}")
    else:
        print(f"错误: 无法保存图片到 '{output_path}'")
    
    return success

def detect_watermark_region(image_path: str) -> Tuple[int, int, int, int]:
    """
    自动检测水印区域（简单实现）
    
    Args:
        image_path (str): 图片路径
    
    Returns:
        tuple: 水印区域坐标 (x, y, width, height)
    """
    
    image = cv2.imread(image_path)
    if image is None:
        return (0, 0, 0, 0)
    
    height, width = image.shape[:2]
    
    # 简单的水印检测逻辑：通常水印在角落
    # 可以检测右下角、左下角、右上角等
    
    # 检测右下角（最常见的logo位置）
    corner_size = min(200, width // 4, height // 4)
    x = width - corner_size
    y = height - corner_size
    
    return (x, y, corner_size, corner_size)

def main():
    """主函数，演示使用方法"""
    
    # 示例用法
    image_path = "example.jpg"  # 替换为实际图片路径
    
    if os.path.exists(image_path):
        # 方法1：使用图像修复
        print("=== 方法1: 图像修复技术 ===")
        success = remove_watermark_inpainting(image_path, "output_inpaint.jpg")
        
        if success:
            print("图像修复完成")
        
        # 方法2：使用图像克隆
        print("\n=== 方法2: 图像克隆技术 ===")
        success = remove_watermark_clone(image_path, "output_clone.jpg")
        
        if success:
            print("图像克隆完成")
    else:
        print(f"示例图片 '{image_path}' 不存在，请替换为实际图片路径")

if __name__ == "__main__":
    main()