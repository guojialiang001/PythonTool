# 视频尾帧提取工具

一个简单的Python工具，用于提取视频文件的最后几帧并保存为图片。

## 功能特性

- 支持常见视频格式（MP4, AVI, MOV等）
- 自动检测视频总帧数
- 可提取倒数多张帧（1-N张）
- 文件名自动包含帧数信息
- 可自定义输出路径和文件名
- 错误处理和友好的提示信息

## 安装依赖

```bash
pip install -r requirements.txt
```

## 使用方法

### 基本使用
```bash
# 提取最后一帧
python extract_last_frame.py 视频文件路径

# 提取倒数5帧
python extract_last_frame.py 视频文件路径 -n 5
```

### 指定输出路径
```bash
# 提取倒数3帧到指定路径
python extract_last_frame.py 视频文件路径 -o 输出图片路径 -n 3
```

### 示例
```bash
# 提取video.mp4的最后一帧，保存为video_last_1_frames_1.jpg
python extract_last_frame.py video.mp4

# 提取video.mp4的倒数5帧，保存为video_last_5_frames_1.jpg, video_last_5_frames_2.jpg等
python extract_last_frame.py video.mp4 -n 5

# 提取video.mp4的倒数3帧，保存为custom_name_1.jpg, custom_name_2.jpg等
python extract_last_frame.py video.mp4 -o custom_name.jpg -n 3
```

## 代码示例

```python
from extract_last_frame import extract_last_frame, main

# 方式1：使用函数方式调用
# 提取最后一帧
success = extract_last_frame("video.mp4", "output.jpg")

# 提取倒数5帧
success = extract_last_frame("video.mp4", "output.jpg", num_frames=5)

# 方式2：使用main函数
# 提取倒数3帧
success = main(video_path="video.mp4", num_frames=3)

if success:
    print("提取成功！")
```

## 支持的视频格式

- MP4 (.mp4)
- AVI (.avi)
- MOV (.mov)
- WMV (.wmv)
- FLV (.flv)
- 其他OpenCV支持的格式

## 文件名规则

- 当不指定输出路径时：`视频名_last_帧数_frames_序号.jpg`
- 当指定输出路径时：`指定名_序号.扩展名`

## 注意事项

- 确保视频文件路径正确
- 需要有足够的磁盘空间保存输出图片
- 支持常见的图片格式（JPG, PNG等）
- 提取的帧数不能超过视频总帧数
- 多帧提取时，文件名会自动添加序号