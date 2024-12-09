from fastapi import FastAPI, Request, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp
import os
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
import humanize
import re
from urllib.parse import quote

app = FastAPI()

# 配置 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 在生产环境中应该设置具体的域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 配置静态文件和模板
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# 下载目录
DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

# 存储下载任务状态
download_tasks = {}

def clean_ansi(text):
    """移除字符串中的 ANSI 颜色代码"""
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', str(text))

def get_video_info(url: str):
    """获取视频信息"""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'format': 'best',
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            duration = info.get('duration')
            duration_str = str(timedelta(seconds=duration)) if duration else "未知"
            return {
                'title': info['title'],
                'duration': duration_str,
                'author': info['uploader'],
                'description': info.get('description', ''),
                'thumbnail': info['thumbnail'],
            }
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

async def download_video(url: str, video_id: str):
    """异步下载视频"""
    download_tasks[video_id] = {'status': 'downloading', 'progress': 0}
    
    def clean_filename(filename):
        """清理文件名，移除不安全的字符"""
        # 替换不安全的字符为下划线
        unsafe_chars = '<>:"/\\|?*\''
        for char in unsafe_chars:
            filename = filename.replace(char, '_')
        return filename

    def progress_hook(d):
        if d['status'] == 'downloading':
            try:
                if '_percent_str' in d:
                    p = d['_percent_str'].strip().replace('%', '')
                    # 移除 ANSI 颜色代码
                    p = ''.join(c for c in p if c.isdigit() or c == '.')
                    download_tasks[video_id]['progress'] = float(p) if p else 0
                elif 'downloaded_bytes' in d and 'total_bytes' in d:
                    # 使用下载字节数计算百分比
                    progress = (d['downloaded_bytes'] / d['total_bytes']) * 100
                    download_tasks[video_id]['progress'] = progress
                else:
                    download_tasks[video_id]['progress'] = 0
            except (ValueError, TypeError, ZeroDivisionError):
                download_tasks[video_id]['progress'] = 0
        elif d['status'] == 'finished':
            download_tasks[video_id]['status'] = 'completed'

    ydl_opts = {
        'format': 'best',
        'outtmpl': str(DOWNLOAD_DIR / '%(title).200s.%(ext)s'),
        'restrictfilenames': True,  # 使用限制性的文件名
        'progress_hooks': [progress_hook],
        'no_color': True,  # 禁用颜色输出
        'quiet': True,     # 减少输出信息
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            await asyncio.get_event_loop().run_in_executor(None, ydl.download, [url])
    except Exception as e:
        download_tasks[video_id]['status'] = 'error'
        download_tasks[video_id]['error'] = str(e)

@app.get("/")
async def home(request: Request):
    """主页"""
    videos = []
    for file in DOWNLOAD_DIR.glob("*"):
        if file.is_file() and not file.name.startswith('.'):
            videos.append({
                'name': file.name,
                'size': humanize.naturalsize(file.stat().st_size),
                'path': f"/downloads/{quote(file.name)}",
                'modified': datetime.fromtimestamp(file.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')
            })
    return templates.TemplateResponse("index.html", {"request": request, "videos": videos})

@app.post("/download")
async def start_download(request: Request):
    """开始下载"""
    try:
        data = await request.json()
        url = data.get('url')
        print(f"Received download request for URL: {url}")  # 添加日志
        video_info = get_video_info(url)
        video_id = str(hash(url))
        asyncio.create_task(download_video(url, video_id))
        return {"video_id": video_id, "info": video_info}
    except Exception as e:
        print(f"Error processing download request: {str(e)}")  # 添加错误日志
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/status/{video_id}")
async def get_status(video_id: str):
    """获取下载状态"""
    if video_id in download_tasks:
        return download_tasks[video_id]
    return {"status": "not_found"}

# 配置下载目录为静态文件服务
app.mount("/downloads", StaticFiles(directory="downloads"), name="downloads") 