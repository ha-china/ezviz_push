# EZVIZ Cloud Push

萤石云信令推送集成，将萤石门铃/猫眼设备接入 Home Assistant。

## 功能

- 接收萤石云 webhook 推送（门铃呼叫、报警、设备状态）
- 自动注册多设备，每个设备独立创建实体
- 传感器：电池电量、WiFi信号、SD卡健康度、SD卡容量、充电状态、报警时间、呼叫时间、呼叫动作（响铃/接听/挂断）、人脸识别ID、报警类型、最近事件、最后活动时间
- 开关：在线状态、门铃按铃、报警触发、布防状态、移动检测开关
- 摄像头：报警图片、呼叫封面图片
- 设备信息重启后持久化保留

## 安装

### 方式一：HACS（推荐）

1. 确保 HA 已安装 [HACS](https://hacs.xyz/)
2. HACS → 集成 → 右下角「自定义仓库」→ 添加：
   - 仓库地址：`https://github.com/ha-china/ezviz_push`
   - 类别：Integration
3. 在 HACS 列表中搜索「EZVIZ Cloud Push」→ 点击「安装」
4. 重启 Home Assistant
5. 设置 → 设备与服务 → 添加集成 → 搜索「EZVIZ Cloud Push」
6. 配置 webhook ID（**务必用随机复杂字符串，不要用默认值**，见下方安全建议）

### 方式二：手动安装

1. 将 `custom_components/ezviz_push/` 复制到 HA 的 `config/custom_components/` 目录
2. 重启 Home Assistant
3. 设置 → 设备与服务 → 添加集成 → 搜索 "EZVIZ Cloud Push"
4. 配置 webhook ID（**务必用随机复杂字符串，不要用默认值**，见下方安全建议）

## 萤石云后台配置

> ⚠️ **前置条件：外网可达**
>
> 萤石云需要从公网把事件推送到你的 HA，因此 HA 必须能被外网访问。请任选其一：
> - **公网 IP**：家庭宽带分配了公网 IPv4/IPv6，路由器做端口转发
> - **内网穿透**：如 frp、ngrok、Cloudflare Tunnel、Tailscale Funnel 等
> - **云服务器反代**：用一台有公网的服务器反向代理到本地 HA
>
> 推送地址必须为 HTTPS，否则萤石云与 HA 均会拒绝连接。

> 🔐 **安全建议：webhook ID 越复杂越好**
>
> webhook 地址（`/api/webhook/<ID>`）暴露在公网，任何人拿到它都能伪装成萤石云推送伪造事件。
> **务必把默认的 `ezviz-push-webhook` 改成一段随机复杂字符串**（如 `ezviz-e8f3a7c2-9b41-4d6e-a5c2-7f3b9d21`），
> 不要用常见或可猜的 ID。安装时手动配置，并同步填写到萤石云后台。

## 人脸识别配置（可选）

> 前提：设备支持人脸识别，且已在萤石 App 开启并登记人脸。萤石云推来的 `faceId` 是设备侧的编码而非姓名，
> 可通过本集成的「选项」配置映射表，让 `face_id` 传感器直接显示人名。

1. 设置 → 设备与服务 → EZVIZ Cloud Push → 选项
2. 在「人脸映射」里按 `faceId:人名` 填写，用逗号或换行分隔多条，例如：
   ```
   1a2b3c:爸爸, 4d5e6f:妈妈
   7g8h9i:自己
   ```
3. 保存后，收到对应 faceId 时 `face_id` 传感器显示人名；未配置的仍显示原始码
4. 原始 faceId 编码始终保留在 `face_id` 传感器的 `raw_face_id` 属性里，可用来做自动化匹配

映射数量不设上限，设备人脸库能登记多少人就能映射多少人。

1. 登录 [萤石开放平台](https://open.ys7.com/)
2. 进入 消息推送 → 新增推送地址
3. URL 填写：`https://你的HA公网地址/api/webhook/你的随机webhookID`
4. 保存后，萤石云会自动推送门铃/猫眼事件到 HA

![萤石云后台配置](ezviz.png)


## 实体列表

每个设备会创建以下实体：

> 💡 **说明：实体值会逐步填充**
>
> 萤石云每次只推送有需要/有变化的信息，因此实体不会一上来就全部有值，而是随着各类事件（门铃呼叫、报警、设备状态上报等）的发生**逐步填充**。
>
> 刚添加集成时，部分实体可能显示为「不可用」或「无值」，这是正常现象。通常在 **24 小时之后**，随着各类事件都至少触发过一次，所有实体才会变为正常。

| 类型 | 实体 | 说明 |
|------|------|------|
| sensor | battery_level | 电池电量 |
| sensor | wifi_signal | WiFi信号强度 |
| sensor | sd_health | SD卡健康度 |
| sensor | sd_capacity | SD卡容量 |
| sensor | charging_status | 充电状态 |
| sensor | alarm_time | 报警时间 |
| sensor | calling_time | 呼叫时间 |
| sensor | calling_action | 呼叫动作（响铃/接听/挂断） |
| sensor | face_id | 人脸识别（需设备支持并开启；显示配置的人名，未配置则显示原始码） |
| sensor | alarm_type | 报警类型 |
| sensor | last_event | 最近事件 |
| sensor | last_seen | 最后活动时间（时间戳） |
| binary_sensor | online | 在线状态 |
| binary_sensor | doorbell_ring | 门铃按铃（30秒自动复位） |
| binary_sensor | alarm | 报警触发（30秒自动复位） |
| binary_sensor | armed | 布防状态 |
| binary_sensor | detection_enabled | 移动检测开关 |
| camera | alarm_picture | 报警图片 |
| camera | calling_picture | 呼叫封面图片 |