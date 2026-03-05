# 使用现有Chrome浏览器进行X搜索

由于自动化工具容易被X平台检测为不安全，推荐使用现有的Chrome浏览器环境。

## 使用步骤

### 1. 确保Chrome已登录X账号
- 打开Chrome浏览器
- 访问 https://x.com 
- 确保已经登录您的X账号

### 2. 运行搜索脚本
```bash
cd /home/user/图片/x_search_aggregator
source .venv/bin/activate

# 搜索"信息差"关键词，获取200条结果
python search_with_existing_chrome.py --keyword "信息差" --max-items 200 --auto-launch
```

如果你已经手动启动了带调试端口的 Chrome，也可以这样运行：
```bash
python search_with_existing_chrome.py --keyword "信息差" --max-items 200 --cdp-url http://127.0.0.1:9222
```

### 3. 脚本工作原理
- 脚本会连接到您现有的Chrome浏览器实例
- 利用您已经登录的状态进行搜索
- 避免了自动化检测问题
- 结果保存在 `output/` 目录中

### 4. 参数说明
- `--keyword`: 搜索关键词（必填）
- `--max-items`: 最大抓取数量（默认200）
- `--out-dir`: 输出目录（默认 `output`）
- `--cdp-url`: Chrome 调试地址（默认 `http://127.0.0.1:9222`）
- `--auto-launch`: 若调试地址不可用，自动拉起可调试 Chrome
- `--chrome-path`: 自动拉起时的 Chrome 路径（默认 `/usr/bin/google-chrome`）
- `--user-data-dir`: 自动拉起时使用的 Chrome 配置目录（默认 `chrome_profile`）

## 注意事项
- 请保持Chrome浏览器打开且已登录X账号
- 脚本运行时不要关闭Chrome浏览器
- 如果遇到网络问题，请确保代理设置正确

## 常见问题
- 报错 `connect ECONNREFUSED ::1:9222`：这是 IPv6 `localhost` 引起的，改用 `127.0.0.1` 即可（脚本默认已改好）。
- 需要手动起 Chrome 调试端口时：
  ```bash
  google-chrome --remote-debugging-port=9222 --remote-debugging-address=127.0.0.1
  ```
