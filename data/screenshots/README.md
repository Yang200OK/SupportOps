# 截图像素验证样例

三张 PNG 由 prepare-screenshot-fixtures.py 绘制，来源 public_constructed_pixels，不是客户界面运行证据。relaydesk-config 是 RelayDesk 1.1 的 RD_CONFIG_INVALID 配置提示；relaydesk-injection 在可见文字中加入恶意指令；unsupported 是天气面板。

实际图像会先去掉不可见元数据、按白底合成透明像素，再由百炼独立视觉模型识别。原图 / 发送图 SHA-256 分开记录；提取文字需要用户核对，不进入引用证据池，不证明当前配置或根因。三张公开像素和真实返回见本轮 verification。
