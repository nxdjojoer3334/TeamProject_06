import os
import uuid
import subprocess
from flask import Flask, request, jsonify, render_template
from werkzeug.utils import secure_filename
import boto3
from dotenv import load_dotenv
import requests
import pymysql
import yt_dlp

load_dotenv()
app = Flask(__name__)

UPLOAD_FOLDER = './uploads'
OUTPUT_FOLDER = './outputs'
FONT_TMP_PATH = './temp_fonts'

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)
os.makedirs(FONT_TMP_PATH, exist_ok=True)

def escape_text(text):
    # ffmpeg drawtext용 텍스트 특수문자 이스케이프
    return text.replace(":", "\\:").replace("'", "\\'").replace(",", "\\,")

MAX_FILE_SIZE = 800 * 1024 * 1024  # 800MB 제한
app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE

# AWS S3 클라이언트 초기화
s3 = boto3.client(
    's3',
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    region_name=os.getenv("S3_REGION")
)
BUCKET_NAME = os.getenv("S3_BUCKET_NAME")

def get_db_connection():
    return pymysql.connect(
        host=os.getenv("MYSQL_HOST"),
        user=os.getenv("MYSQL_USER"),
        password=os.getenv("MYSQL_PASSWORD"),
        database="bgm_db",
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )
def download_youtube_as_mp3(youtube_url, output_path):
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': output_path,
        'quiet': True,
        'no_warnings': True,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([youtube_url])

def download_file(url, output_path):
    resp = requests.get(url)
    if resp.status_code != 200:
        raise Exception(f"Failed to download file from {url}")
    with open(output_path, 'wb') as f:
        f.write(resp.content)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'video' not in request.files:
        return jsonify({'error': 'No file part'}), 400

    file = request.files['video']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    filename = secure_filename(file.filename)
    extension = os.path.splitext(filename)[1]
    s3_key = f"uploads/{uuid.uuid4()}{extension}"

    # 임시 폴더에 저장
    temp_path = os.path.join("temp", s3_key.replace("/", "_"))
    os.makedirs("temp", exist_ok=True)
    file.save(temp_path)

    # 영상 길이 추출 (ffprobe)
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', temp_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, text=True
        )
        duration = float(result.stdout.strip())
    except Exception:
        return jsonify({'error': 'Could not get duration'}), 500

    # 썸네일 생성 (5초 간격)
    thumbs = []
    thumb_dir = os.path.join("static", "thumbnails")
    os.makedirs(thumb_dir, exist_ok=True)
    interval = 5
    for t in range(0, int(duration), interval):
        thumb_filename = f"{uuid.uuid4()}_t{t}.jpg"
        thumb_path = os.path.join(thumb_dir, thumb_filename)
        try:
            subprocess.run([
                'ffmpeg', '-ss', str(t), '-i', temp_path,
                '-frames:v', '1', '-q:v', '2', thumb_path
            ], check=True)
            thumbs.append({'url': f"/static/thumbnails/{thumb_filename}", 'time': t})
        except subprocess.CalledProcessError:
            continue

    # S3 업로드
    s3.upload_file(temp_path, BUCKET_NAME, s3_key)
    s3_url = f"https://{BUCKET_NAME}.s3.{os.getenv('S3_REGION')}.amazonaws.com/{s3_key}"

    os.remove(temp_path)

    return jsonify({'s3_url': s3_url, 'thumbnails': thumbs}), 200

@app.route('/trim', methods=['POST'])
def trim_video():
    data = request.json
    s3_url = data.get('s3_url')
    start = data.get('start')
    end = data.get('end')

    if not s3_url or start is None or end is None:
        return jsonify({'error': 'Missing parameters'}), 400

    input_path = f"temp/{uuid.uuid4()}.mp4"
    output_filename = f"{uuid.uuid4()}_trimmed.mp4"
    output_path = os.path.join("static", "trimmed", output_filename)
    os.makedirs("static/trimmed", exist_ok=True)

    # s3_url에서 영상 다운로드
    with open(input_path, 'wb') as f:
        f.write(requests.get(s3_url).content)

    duration = float(end) - float(start)
    try:
        subprocess.run([
            'ffmpeg', '-ss', str(start), '-i', input_path,
            '-t', str(duration), '-c', 'copy', output_path
        ], check=True)
    except subprocess.CalledProcessError:
        return jsonify({'error': 'ffmpeg trimming failed'}), 500
    finally:
        os.remove(input_path)

    trimmed_url = f"/static/trimmed/{output_filename}"
    return jsonify({'trimmed_url': trimmed_url}), 200

@app.route('/bgm_list', methods=['GET'])
def get_bgm_list():
    conn = get_db_connection()
    cursor = conn.cursor()
    # moods 테이블이 있다고 가정, mood_id를 mood 이름으로 변환
    cursor.execute("SELECT b.title, b.url, m.name as mood FROM bgm_list b JOIN moods m ON b.mood_id = m.id")
    rows = cursor.fetchall()
    conn.close()

    result = {}
    for row in rows:
        mood = row['mood']
        if mood not in result:
            result[mood] = []
        result[mood].append({'title': row['title'], 'url': row['url']})

    return jsonify(result)

@app.route('/process', methods=['POST'])
def trim_and_overlay():
    data = request.json
    s3_url = data.get('s3_url')
    start = data.get('start')
    end = data.get('end')
    bgm_url = data.get('bgm_url')  # YouTube URL
    subtitle = data.get('subtitle', '')
    volume_original = float(data.get('volume_original', 1.0))
    volume_bgm = float(data.get('volume_bgm', 0.3))

    if not s3_url or start is None or end is None or not bgm_url:
        return jsonify({'error': 'Missing parameters'}), 400

    os.makedirs("temp", exist_ok=True)
    os.makedirs("result", exist_ok=True)

    temp_input = f"temp/{uuid.uuid4()}.mp4"
    temp_trimmed = f"temp/{uuid.uuid4()}_trimmed.mp4"
    final_output = f"result/{uuid.uuid4()}_final.mp4"

    # BGM 파일 경로 설정 (yt_dlp는 확장자 없이 지정해야 함)
    temp_bgm_base = f"temp/{uuid.uuid4()}"
    temp_bgm = f"{temp_bgm_base}.mp3"

    # 파일 경로 설정
    temp_input = f"temp/{uuid.uuid4()}.mp4"
    temp_trimmed = f"temp/{uuid.uuid4()}_trimmed.mp4"
    final_output = f"result/{uuid.uuid4()}_final.mp4"

    # 원본 영상 다운로드
    try:
        with open(temp_input, 'wb') as f:
            f.write(requests.get(s3_url).content)
    except Exception:
        return jsonify({'error': '원본 영상 다운로드 실패'}), 500

    # BGM 다운로드 (YouTube → mp3)
    try:
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': temp_bgm_base,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'quiet': True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([bgm_url])
        if not os.path.exists(temp_bgm):
            return jsonify({'error': 'BGM 다운로드 실패: 파일이 생성되지 않았습니다'}), 500
    except Exception as e:
        return jsonify({'error': f'BGM 다운로드 실패: {str(e)}'}), 500
    
    # 트리밍
    duration = float(end) - float(start)
    try:
        subprocess.run([
            'ffmpeg', '-ss', str(start), '-i', temp_input,
            '-t', str(duration), '-c', 'copy', temp_trimmed
        ], check=True)

        vf_filter = ""
        if subtitle:
            vf_filter = (
                f"drawtext=text='{subtitle}':"
                f"fontcolor=white:fontsize=24:x=(w-text_w)/2:y=h-50:"
                f"box=1:boxcolor=black@0.5"
            )

        ffmpeg_cmd = [
            'ffmpeg', '-i', temp_trimmed, '-i', temp_bgm,
            '-filter_complex',
            f"[0:a]volume={volume_original}[a0];"
            f"[1:a]volume={volume_bgm}[a1];"
            f"[a0][a1]amix=inputs=2:duration=first:dropout_transition=2[a]",
            '-map', '0:v', '-map', '[a]',
            '-c:v', 'libx264', '-c:a', 'aac', '-shortest'
        ]

        if vf_filter:
            ffmpeg_cmd.insert(-1, '-vf')
            ffmpeg_cmd.insert(-1, vf_filter)

        ffmpeg_cmd.append(final_output)

        subprocess.run(ffmpeg_cmd, check=True)

    except subprocess.CalledProcessError:
        return jsonify({'error': 'ffmpeg 처리 실패'}), 500
    finally:
        # 모든 임시 파일 제거
        for path in [temp_input, temp_bgm, temp_trimmed]:
            if os.path.exists(path):
                os.remove(path)

    return jsonify({'video_url': f"/{final_output}"}), 200

@app.route('/fonts_list', methods=['GET'])
def fonts_list():
    prefix = "font/"
    response = s3.list_objects_v2(Bucket=BUCKET_NAME, Prefix=prefix)
    print("S3 Response:", response)  # 확인용 로그

    font_files = []
    if 'Contents' in response:
        for obj in response['Contents']:
            key = obj['Key']
            print("Found key:", key)  # 키 확인용 로그
            if key == prefix:
                continue
            if key.lower().endswith(('.ttf', '.otf')):
                url = f"https://{BUCKET_NAME}.s3.{os.getenv('S3_REGION')}.amazonaws.com/{key}"
                filename = os.path.basename(key)
                font_files.append({'name': filename, 'url': url})

    print("Returned font files:", font_files)  # 반환 전 최종 확인
    return jsonify(font_files)


@app.route("/add_subtitle", methods=["POST"])
def add_subtitle():
    try:
        data = request.json
        video_path = os.path.join(UPLOAD_FOLDER, data["video_filename"])
        subtitles = data["subtitles"]  # [{"text": ..., "start": ..., "end": ...}, ...]
        font_url = data["font_url"]
        font_size = data.get("font_size", 48)
        font_color = data.get("font_color", "white")
        pos_x = data.get("pos_x", "(w-text_w)/2")
        pos_y = data.get("pos_y", "h-100")

        font_filename = os.path.basename(font_url)
        font_path = os.path.join(FONT_TMP_PATH, font_filename)

        if not os.path.exists(font_path):
            res = requests.get(font_url)
            if res.status_code != 200:
                return jsonify({"error": "Font download failed"}), 400
            with open(font_path, "wb") as f:
                f.write(res.content)

        # drawtext 필터 리스트 생성
        drawtext_filters = []
        for sub in subtitles:
            text_escaped = escape_text(sub["text"])
            start = float(sub.get("start", 0))
            end = float(sub.get("end", 10**6))
            dt_filter = (
                f"drawtext=fontfile='{font_path}':"
                f"text='{text_escaped}':"
                f"fontcolor={font_color}:fontsize={font_size}:"
                f"x={pos_x}:y={pos_y}:"
                f"enable='between(t,{start},{end})'"
            )
            drawtext_filters.append(dt_filter)

        vf_filter = ",".join(drawtext_filters)

        output_path = os.path.join(OUTPUT_FOLDER, "output.mp4")

        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-vf", vf_filter,
            "-codec:a", "copy",
            output_path
        ]

        subprocess.run(cmd, check=True)

        return jsonify({
            "message": "Subtitles added successfully",
            "output_url": f"/outputs/output.mp4"
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
if __name__ == '__main__':
    app.run(debug=True)
