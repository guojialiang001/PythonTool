from extract_last_frame import main, extract_last_frame
from remove_watermark import remove_watermark_inpainting, remove_watermark_clone

# 提取倒数5帧
# success = main(video_path="D:\\GPT浏览器下载\\kling_20251202_作品_图片1以图片为首帧__2730_0.mp4" , num_frames=5)



success = main(video_path="122_202512061458_bs.mp4" , num_frames=230)



# 或直接使用函数
# success = extract_last_frame("video.mp4", num_frames=5)
#
# if success:
#     print("提取成功！")



# 使用不同修复方法
# success = remove_watermark_inpainting("Cyberpunk_Data_Center_Megalopolis_Aerial.png", method='ns')

# 使用图像克隆技术
# success = remove_watermark_clone("Cyberpunk_Data_Center_Megalopolis_Aerial.png", "output_clone.jpg")