# -*- coding: utf-8 -*-

import smtplib
import traceback
from email.mime.image import MIMEImage
from email.mime.text import MIMEText
from email.utils import formataddr
from email.mime.multipart import MIMEMultipart
sender = '3205446478@qq.com'
password = 'ayzorzjrspnkddhj'
user = '447326870@qq.com'
#
#
# def mail():
#     try:
#         print("1. 开始创建邮件内容...")
#
#         # 正确创建 MIMEText 对象
#         msg = MIMEText("2435", 'plain', 'utf-8')  # 邮件正文
#         msg['From'] = formataddr(["你是谁", sender])
#         msg['To'] = formataddr(["LJ", user])
#         msg['Subject'] = "Python发送邮件测试-132"
#
#         # msg['text'] = "2435"
#         # msg.attach(MIMEText('这是我的第二个Python邮件程序', 'plain', 'utf-8'))
#
#         # message = MIMEText(msg)  # 邮件正文
#         print("✓ 基础邮件内容创建成功")
#         print("2. 添加附件...")
#
#
#         # # 检查文件是否存在
#         # try:
#         #     with open('C:\\Users\\86151\\Desktop\\新建 文本文档 (4).txt', 'rb') as f:
#         #         file_content = f.read()
#         #     att1 = MIMEText(file_content, 'base64', 'utf-8')
#         #     att1["Content-Type"] = 'application/octet-stream'
#         #     att1["Content-Disposition"] = 'attachment; filename="text.txt"'
#         #     msg.attach(att1)
#         #     print("✓ 附件添加成功")
#         # except FileNotFoundError:
#         #     print("✗ 附件文件 text.txt 不存在")
#         #     return False
#
#         # print("3. 添加HTML内容和图片...")
#         # mail_msg = """
#         # <p>Python 邮件发送测试...</p>
#         # <p><a href="http://www.baidu.com">百度搜索</a></p>
#         # <p><img src="cid:image1"></p>
#         # """
#         # msg.attach(MIMEText(mail_msg, 'html', 'utf-8'))
#         #
#         # # 检查图片文件是否存在
#         # try:
#         #     with open('C:\\Users\\86151\\Desktop\\微信图片_2025-09-04_135918_718.png', 'rb') as fp:
#         #         msgImage = MIMEImage(fp.read())
#         #     msgImage.add_header('Content-ID', '<image1>')
#         #     msg.attach(msgImage)
#         #     print("✓ 图片添加成功")
#         # except FileNotFoundError:
#         #     print("✗ 图片文件 test.png 不存在")
#         #     return False
#
#         print("4. 连接SMTP服务器...")
#         server = smtplib.SMTP_SSL("smtp.qq.com", 465)
#         print("✓ SMTP连接成功")
#
#         print("5. 登录邮箱...")
#         server.login(sender, password)
#         print("✓ 登录成功")
#
#         print("6. 发送邮件...")
#         server.sendmail(sender, [user], msg.as_string())
#         print("✓ 邮件发送成功")
#
#         print("7. 断开连接...")
#         server.quit()
#         print("✓ 连接已关闭")
#
#         return True
#
#     except smtplib.SMTPAuthenticationError as e:
#         print(f"✗ SMTP认证失败: {e}")
#         return False
#     except smtplib.SMTPException as e:
#         print(f"✗ SMTP错误: {e}")
#         return False
#     except Exception as e:
#         print(f"✗ 未知错误: {type(e).__name__}: {e}")
#         traceback.print_exc()
#         return False
#
#
# print("开始发送邮件...")
# ret = mail()
# if ret:
#     print("发送邮件成功")
# else:
#     print("发送邮件失败")
#




def mail(content=None, from_name=None, to_name=None, subject=None):
    # 检查参数是否为空
    if not all([content, from_name, subject]):
        print("[X] 邮件参数不能为空")
        return False

    try:
        print("1. 开始创建邮件内容...")

        # 使用封装方法创建邮件
        msg = create_email_message(
            content=content,
            from_name=from_name,
            to_name=to_name,
            subject=subject
        )
        # 后续发送逻辑（示例）
        # send_email(msg)

        print("[OK] 基础邮件内容创建成功")

        print("4. 连接SMTP服务器...")
        server = smtplib.SMTP_SSL("smtp.qq.com", 465)
        print("[OK] SMTP连接成功")

        print("5. 登录邮箱...")
        server.login(sender, password)
        print("[OK] 登录成功")

        print("6. 发送邮件...")
        server.sendmail(sender, [user], msg.as_string())
        print("[OK] 邮件发送成功")

        print("7. 断开连接...")
        server.quit()
        print("[OK] 连接已关闭")

        return True

    except smtplib.SMTPAuthenticationError as e:
        print(f"[X] SMTP认证失败: {e}")
        return False
    except smtplib.SMTPException as e:
        print(f"[X] SMTP错误: {e}")
        return False
    except Exception as e:
        print(f"[X] 未知错误: {type(e).__name__}: {e}")
        traceback.print_exc()
        return False


def create_email_message(content, from_name, to_name, subject, email_type='plain'):
    """
    创建邮件消息对象
    :param content: 邮件正文内容
    :param from_name: 发件人名称
    :param to_name: 收件人名称
    :param subject: 邮件主题
    :param email_type: 邮件类型 'plain' 或 'html'
    :return: MIMEText 对象
    """
    msg = MIMEText(content, email_type, 'utf-8')
    msg['From'] = formataddr([from_name, sender])
    msg['To'] = formataddr([to_name, user])
    msg['Subject'] = subject
    return msg

