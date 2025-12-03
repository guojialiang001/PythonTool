import cv2
import os
import argparse
import sys

def extract_last_frame(video_path, output_path=None, num_frames=1):
    """
    提取视频的最后几帧并保存为图片
    
    Args:
        video_path (str): 视频文件路径
        output_path (str, optional): 输出图片路径。如果为None，则使用视频文件名+'_last_frame.jpg'
        num_frames (int, optional): 要提取的帧数，默认为1（最后一帧）
    
    Returns:
        bool: 成功返回True，失败返回False
    """
    
    # 检查视频文件是否存在
    if not os.path.exists(video_path):
        print(f"错误: 视频文件 '{video_path}' 不存在")
        return False
    
    # 打开视频文件
    cap = cv2.VideoCapture(video_path)
    
    if not cap.isOpened():
        print(f"错误: 无法打开视频文件 '{video_path}'")
        return False
    
    # 获取视频总帧数
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    
    if total_frames == 0:
        print("错误: 视频文件没有帧")
        cap.release()
        return False
    
    print(f"视频总帧数: {total_frames}")
    
    # 确保提取的帧数不超过总帧数
    if num_frames > total_frames:
        print(f"警告: 请求提取 {num_frames} 帧，但视频只有 {total_frames} 帧，将提取所有帧")
        num_frames = total_frames
    
    # 计算起始帧位置
    start_frame = max(0, total_frames - num_frames)
    
    success_count = 0
    
    # 提取多帧
    for i in range(num_frames):
        frame_number = start_frame + i
        
        # 设置到指定帧
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
        
        # 读取帧
        ret, frame = cap.read()
        
        if not ret:
            print(f"错误: 无法读取第 {frame_number + 1} 帧")
            continue
        
        # 生成输出路径
        if output_path is None:
            video_name = os.path.splitext(os.path.basename(video_path))[0]
            output_path = f"{video_name}_last_{num_frames}_frames_{i+1}.jpg"
        else:
            # 如果指定了输出路径，为多帧添加序号
            base_name, ext = os.path.splitext(output_path)
            output_path = f"{base_name}_{i+1}{ext}"
        
        # 确保输出目录存在
        output_dir = os.path.dirname(output_path)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir)
        
        # 保存图片
        success = cv2.imwrite(output_path, frame)
        
        if success:
            print(f"成功提取第 {frame_number + 1} 帧到: {output_path}")
            print(f"图片尺寸: {frame.shape[1]}x{frame.shape[0]}")
            success_count += 1
        else:
            print(f"错误: 无法保存图片到 '{output_path}'")
    
    print(f"成功提取 {success_count}/{num_frames} 帧")
    
    cap.release()
    return success_count > 0

def main(video_path=None, output_path=None, num_frames=1):
    """
    主函数，支持命令行参数和代码调用两种方式
    
    Args:
        video_path (str, optional): 视频文件路径，如果为None则从命令行获取
        output_path (str, optional): 输出图片路径，如果为None则自动生成
        num_frames (int, optional): 要提取的帧数，默认为1（最后一帧）
    """
    
    # 如果提供了参数，直接使用
    if video_path is not None:
        return extract_last_frame(video_path, output_path, num_frames)
    
    # 否则从命令行获取参数
    if len(sys.argv) < 2:
        print("用法: python extract_last_frame.py <视频文件路径> [-o 输出图片路径]")
        print("示例: python extract_last_frame.py video.mp4")
        print("示例: python extract_last_frame.py video.mp4 -o output.jpg")
        return False
    
    parser = argparse.ArgumentParser(description='提取视频的最后几帧')
    parser.add_argument('video_path', help='视频文件路径')
    parser.add_argument('-o', '--output', help='输出图片路径（可选）')
    parser.add_argument('-n', '--num_frames', type=int, default=1, help='要提取的帧数，默认为1（最后一帧）')
    
    # 如果只有一个参数且不是-h/--help，尝试直接处理
    if len(sys.argv) == 2 and sys.argv[1] not in ['-h', '--help']:
        # 直接调用函数，不使用argparse
        return extract_last_frame(sys.argv[1])
    else:
        # 使用argparse处理多个参数
        args = parser.parse_args()
        return extract_last_frame(args.video_path, args.output, args.num_frames)

if __name__ == "__main__":
    main()