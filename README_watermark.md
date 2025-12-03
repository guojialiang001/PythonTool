# 图片去水印工具

一个基于OpenCV的Python工具，提供多种方法去除图片中的水印。

## 功能特性

- 支持两种去水印技术：图像修复和图像克隆
- 可指定水印区域或自动检测
- 支持多种图片格式
- 详细的错误处理和进度反馈

## 安装依赖

```bash
pip install -r requirements.txt
```

## 使用方法

### 基本使用

```python
from remove_watermark import remove_watermark_inpainting, remove_watermark_clone

# 方法1：使用图像修复技术
success = remove_watermark_inpainting("input.jpg", "output_inpaint.jpg")

# 方法2：使用图像克隆技术
success = remove_watermark_clone("input.jpg", "output_clone.jpg")
```

### 指定水印区域

```python
# 指定水印区域坐标 (x, y, width, height)
watermark_region = (100, 100, 200, 50)
success = remove_watermark_inpainting("input.jpg", "output.jpg", watermark_region)
```

### 高级参数

```python
# 使用不同的修复方法
success = remove_watermark_inpainting("input.jpg", method='ns')  # 使用Navier-Stokes方法

# 指定克隆的源区域和目标区域
source_region = (50, 50, 100, 100)
target_region = (200, 200, 100, 100)
success = remove_watermark_clone("input.jpg", source_region=source_region, target_region=target_region)
```

## 技术说明

### 图像修复 (Inpainting)
- **原理**: 使用周围像素信息来填充水印区域
- **方法**: 
  - `telea`: Telea算法，速度较快
  - `ns`: Navier-Stokes算法，质量较好但较慢
- **适用场景**: 水印区域较小，周围背景纹理简单

### 图像克隆 (Seamless Cloning)
- **原理**: 从图片其他区域复制相似纹理来覆盖水印
- **优点**: 保持纹理一致性
- **适用场景**: 水印区域较大，需要保持背景纹理

## 参数说明

### remove_watermark_inpainting 函数
- `image_path`: 输入图片路径
- `output_path`: 输出图片路径（可选）
- `watermark_region`: 水印区域坐标 (x, y, width, height)（可选）
- `method`: 修复方法，'telea' 或 'ns'（默认 'telea'）

### remove_watermark_clone 函数
- `image_path`: 输入图片路径
- `output_path`: 输出图片路径（可选）
- `source_region`: 源区域坐标 (x, y, width, height)（可选）
- `target_region`: 目标区域坐标 (x, y, width, height)（可选）

## 使用示例

### 示例1：去除右下角水印
```python
from remove_watermark import remove_watermark_inpainting

# 假设水印在右下角 200x100 像素区域
watermark_region = (image_width - 200, image_height - 100, 200, 100)
success = remove_watermark_inpainting("photo.jpg", "clean_photo.jpg", watermark_region)
```

### 示例2：批量处理
```python
import os
from remove_watermark import remove_watermark_inpainting

image_folder = "images/"
output_folder = "cleaned_images/"

for filename in os.listdir(image_folder):
    if filename.endswith(('.jpg', '.png', '.jpeg')):
        input_path = os.path.join(image_folder, filename)
        output_path = os.path.join(output_folder, f"clean_{filename}")
        
        success = remove_watermark_inpainting(input_path, output_path)
        if success:
            print(f"成功处理: {filename}")
```

## 注意事项

1. **水印区域定位**: 工具提供简单的水印区域检测，但对于复杂水印可能需要手动指定区域
2. **图片质量**: 修复效果取决于水印大小、位置和背景复杂度
3. **文件格式**: 支持常见图片格式（JPG, PNG, BMP等）
4. **内存使用**: 处理大图片时可能需要较多内存

## 文件结构

- `remove_watermark.py`: 主要的去水印功能实现
- `requirements.txt`: 项目依赖
- `README_watermark.md`: 本文档

## 扩展建议

- 可以添加GUI界面方便区域选择
- 可以集成机器学习模型进行更精确的水印检测
- 可以添加批量处理功能