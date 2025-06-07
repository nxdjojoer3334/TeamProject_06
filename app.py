import os
import pymysql
import boto3
import uuid
from yt_dlp import YoutubeDL
from flask import Flask, request, jsonify, render_template, send_from_directory
from dotenv import load_dotenv
import subprocess
from datetime import datetime

# .env 파일 로드
load_dotenv()

app = Flask(__name__)

# 환경 변수 설정
DB_HOST = os.getenv('DB_HOST')
DB_USER = os.getenv('DB_USER')
DB_PASSWORD = os.getenv('DB_PASSWORD')
DB_NAME = os.getenv('DB_NAME')

AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY')
AWS_REGION = os.getenv('AWS_REGION')
S3_BUCKET_NAME = os.getenv('S3_BUCKET_NAME')

# S3 클라이언트 초기화
s3 = boto3.client(
    's3',
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_REGION
)

# 파일 업로드 경로 설정
UPLOAD_FOLDER = 'uploads'
TRIMMED_FOLDER = 'trimmed_videos'
FINAL_FOLDER = 'final_videos'
BGM_AUDIO_FOLDER = 'bgm_audios'
FONT_CACHE_FOLDER = 'font_cache'

# 필요한 디렉토리 생성
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(TRIMMED_FOLDER, exist_ok=True)
os.makedirs(FINAL_FOLDER, exist_ok=True)
os.makedirs(BGM_AUDIO_FOLDER, exist_ok=True)
os.makedirs(FONT_CACHE_FOLDER, exist_ok=True)

# 폰트 파일을 S3에서 로컬로 다운로드 (자막 처리를 위해)
# S3 버킷에 'fonts/' 경로에 폰트 파일들이 미리 업로드되어 있어야 합니다.
def download_fonts_from_s3():
    try:
        response = s3.list_objects_v2(Bucket=S3_BUCKET_NAME, Prefix='fonts/')
        if 'Contents' in response:
            for obj in response['Contents']:
                if not obj['Key'].endswith('/'): # 폴더가 아닌 파일만 다운로드
                    font_key = obj['Key']
                    font_filename = os.path.basename(font_key)
                    local_font_path = os.path.join(FONT_CACHE_FOLDER, font_filename)
                    if not os.path.exists(local_font_path):
                        print(f"Downloading font: {font_key} to {local_font_path}")
                        s3.download_file(S3_BUCKET_NAME, font_key, local_font_path)
        print("Font download completed.")
    except Exception as e:
        print(f"Error downloading fonts from S3: {e}")

# 애플리케이션 시작 시 폰트 다운로드
with app.app_context():
    download_fonts_from_s3()

# DB 연결 함수
def get_db_connection():
    conn = pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        cursorclass=pymysql.cursors.DictCursor
    )
    return conn

@app.route('/')
def index():
    return render_template('index.html')

# 사용자가 업로드한 비디오 목록 가져오기 (DB에서)
@app.route('/get_user_videos', methods=['GET'])
def get_user_videos():
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            # 새로운 videos 테이블 스키마에 맞춰 컬럼 선택
            sql = "SELECT id, original_filename, s3_url, duration, status, created_at FROM videos ORDER BY created_at DESC"
            cursor.execute(sql)
            videos = cursor.fetchall()
        return jsonify(videos)
    except Exception as e:
        print(f"Error fetching user videos: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

# 사용 가능한 BGM 목록 가져오기 (DB에서)
@app.route('/get_bgm_list', methods=['GET'])
def get_bgm_list():
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            sql = "SELECT id, title, filename, s3_url, duration FROM bgm_audios ORDER BY title ASC"
            cursor.execute(sql)
            bgm_list = cursor.fetchall()
        return jsonify(bgm_list)
    except Exception as e:
        print(f"Error fetching BGM list: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

# 사용 가능한 폰트 목록 가져오기 (로컬 캐시에서)
@app.route('/get_available_fonts', methods=['GET'])
def get_available_fonts():
    try:
        fonts = [f for f in os.listdir(FONT_CACHE_FOLDER) if f.lower().endswith(('.ttf', '.otf'))]
        return jsonify(fonts)
    except Exception as e:
        print(f"Error getting available fonts: {e}")
        return jsonify({'error': str(e)}), 500

# 비디오 업로드
@app.route('/upload_video', methods=['POST'])
def upload_video():
    if 'video' not in request.files:
        return jsonify({'error': 'No video file provided'}), 400

    video_file = request.files['video']
    if video_file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    unique_filename = str(uuid.uuid4()) + os.path.splitext(video_file.filename)[1]
    local_path = os.path.join(UPLOAD_FOLDER, unique_filename)
    video_file.save(local_path)

    try:
        # 비디오 길이 측정 (ffprobe 사용)
        cmd = [
            'ffprobe',
            '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            local_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        duration = float(result.stdout.strip())

        # S3에 업로드
        s3_key = f"{UPLOAD_FOLDER}/{unique_filename}"
        s3.upload_file(local_path, S3_BUCKET_NAME, s3_key)
        s3_url = f"https://{S3_BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/{s3_key}"

        # DB에 정보 저장
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                # 새로운 videos 테이블 스키마에 맞춰 INSERT
                sql = "INSERT INTO videos (original_filename, s3_key, s3_url, duration, status, created_at) VALUES (%s, %s, %s, %s, %s, %s)"
                cursor.execute(sql, (video_file.filename, s3_key, s3_url, duration, 'uploaded', datetime.now()))
                conn.commit()
                video_id = cursor.lastrowid
        finally:
            conn.close()

        # 로컬 파일 삭제
        os.remove(local_path)

        return jsonify({
            'message': 'Video uploaded successfully',
            'video_id': video_id,
            's3_url': s3_url,
            'original_filename': video_file.filename,
            'duration': duration
        }), 200

    except subprocess.CalledProcessError as e:
        print(f"FFprobe error: {e.stderr}")
        os.remove(local_path)
        return jsonify({'error': 'Failed to process video (ffprobe error)'}), 500
    except Exception as e:
        print(f"Upload error: {e}")
        if os.path.exists(local_path):
            os.remove(local_path)
        return jsonify({'error': str(e)}), 500

# 비디오 트리밍
@app.route('/trim_video', methods=['POST'])
def trim_video():
    data = request.json
    video_s3_url = data.get('video_s3_url')
    video_id = data.get('video_id')
    start_time = data.get('start_time')
    end_time = data.get('end_time')

    if not all([video_s3_url, video_id is not None, start_time is not None, end_time is not None]):
        return jsonify({'error': 'Missing trimming parameters'}), 400

    unique_id = str(uuid.uuid4())
    input_local_path = os.path.join(UPLOAD_FOLDER, f"temp_input_{unique_id}.mp4")
    output_local_path = os.path.join(TRIMMED_FOLDER, f"trimmed_video_{unique_id}.mp4")

    try:
        # S3에서 원본 비디오 다운로드
        s3.download_file(S3_BUCKET_NAME, video_s3_url.split('/')[-2] + '/' + video_s3_url.split('/')[-1], input_local_path)

        # FFmpeg로 트리밍
        cmd = [
            'ffmpeg',
            '-i', input_local_path,
            '-ss', str(start_time),
            '-to', str(end_time),
            '-c', 'copy',
            output_local_path
        ]
        subprocess.run(cmd, check=True, capture_output=True)

        # 트리밍된 비디오를 S3에 업로드
        new_s3_key = f"{TRIMMED_FOLDER}/{os.path.basename(output_local_path)}"
        s3.upload_file(output_local_path, S3_BUCKET_NAME, new_s3_key)
        new_s3_url = f"https://{S3_BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/{new_s3_key}"

        # DB 업데이트
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                sql = "UPDATE videos SET s3_url = %s, status = %s WHERE id = %s"
                cursor.execute(sql, (new_s3_url, 'trimmed', video_id))
                conn.commit()
        finally:
            conn.close()

        # 로컬 파일 삭제
        os.remove(input_local_path)
        os.remove(output_local_path)

        return jsonify({
            'message': 'Video trimmed successfully',
            'video_id': video_id,
            'new_s3_url': new_s3_url,
            'status': 'trimmed'
        }), 200

    except subprocess.CalledProcessError as e:
        print(f"FFmpeg error during trimming: {e.stderr}")
        return jsonify({'error': 'Failed to trim video (ffmpeg error)'}), 500
    except Exception as e:
        print(f"Trimming error: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        if os.path.exists(input_local_path):
            os.remove(input_local_path)
        if os.path.exists(output_local_path):
            os.remove(output_local_path)


# BGM 다운로드 (YouTube에서)
# app.py 상단에 `from yt_dlp import YoutubeDL` 추가
# 또는 `subprocess`로 `yt-dlp` 명령을 직접 실행
from yt_dlp import YoutubeDL

@app.route('/download_bgm_from_youtube', methods=['POST'])
def download_bgm_from_youtube():
    data = request.json
    youtube_url = data.get('youtube_url')

    if not youtube_url:
        return jsonify({'error': 'No YouTube URL provided'}), 400

    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'outtmpl': os.path.join(BGM_AUDIO_FOLDER, '%(title)s.%(ext)s'),
        'restrictfilenames': True, # 안전한 파일명
    }

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(youtube_url, download=True)
            # 다운로드된 파일의 실제 경로와 제목 찾기
            # info_dict에서 실제 파일명 경로를 얻는 방식은 yt-dlp 버전에 따라 다를 수 있습니다.
            # 가장 안전한 방법은 info_dict에서 title과 id를 조합하는 것입니다.
            # 예: actual_filename = os.path.join(BGM_AUDIO_FOLDER, ydl.prepare_filename(info_dict))
            # 또는 info_dict.get('requested_downloads')[0].get('filepath') 등을 사용
            # 여기서는 title을 filename으로 간주합니다.
            
            audio_title = info_dict.get('title')
            audio_filename = f"{audio_title}.mp3" # yt-dlp 기본 mp3 확장자
            local_audio_path = os.path.join(BGM_AUDIO_FOLDER, audio_filename)
            
            # 파일이 실제로 다운로드되었는지 확인
            if not os.path.exists(local_audio_path):
                # 다른 파일명 패턴을 시도하거나 에러 처리
                potential_files = [f for f in os.listdir(BGM_AUDIO_FOLDER) if audio_title in f and f.endswith('.mp3')]
                if potential_files:
                    local_audio_path = os.path.join(BGM_AUDIO_FOLDER, potential_files[0])
                else:
                    raise Exception("Downloaded audio file not found locally.")

            # S3에 업로드
            s3_key = f"{BGM_AUDIO_FOLDER}/{os.path.basename(local_audio_path)}"
            s3.upload_file(local_audio_path, S3_BUCKET_NAME, s3_key)
            s3_url = f"https://{S3_BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/{s3_key}"

            # 오디오 길이 측정 (ffprobe 사용)
            cmd = [
                'ffprobe',
                '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                local_audio_path
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            duration = float(result.stdout.strip())

            # DB에 정보 저장
            conn = get_db_connection()
            try:
                with conn.cursor() as cursor:
                    sql = "INSERT INTO bgm_audios (title, filename, s3_key, s3_url, youtube_url, duration, uploaded_at) VALUES (%s, %s, %s, %s, %s, %s, %s)"
                    cursor.execute(sql, (audio_title, os.path.basename(local_audio_path), s3_key, s3_url, youtube_url, duration, datetime.now()))
                    conn.commit()
                    bgm_id = cursor.lastrowid
            finally:
                conn.close()

            # 로컬 파일 삭제
            os.remove(local_audio_path)

            return jsonify({
                'message': 'BGM downloaded and uploaded successfully',
                'bgm_id': bgm_id,
                'title': audio_title,
                'filename': os.path.basename(local_audio_path),
                's3_url': s3_url,
                'duration': duration
            }), 200

    except Exception as e:
        print(f"BGM download/upload error: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        # 혹시 모를 로컬 임시 파일 정리
        if 'local_audio_path' in locals() and os.path.exists(local_audio_path):
            os.remove(local_audio_path)


# BGM 추가 (오디오 믹싱)
@app.route('/add_bgm_to_video', methods=['POST'])
def add_bgm_to_video():
    data = request.json
    video_s3_url = data.get('video_s3_url')
    video_id = data.get('video_id')
    bgm_s3_url = data.get('bgm_s3_url')
    bgm_volume = data.get('bgm_volume', 0.5) # BGM 볼륨 기본값 0.5
    video_volume = data.get('video_volume', 0.5) # 원본 비디오 오디오 볼륨 기본값 0.5

    if not all([video_s3_url, video_id is not None, bgm_s3_url]):
        return jsonify({'error': 'Missing BGM adding parameters'}), 400

    unique_id = str(uuid.uuid4())
    video_input_local_path = os.path.join(UPLOAD_FOLDER, f"temp_video_{unique_id}.mp4")
    bgm_input_local_path = os.path.join(BGM_AUDIO_FOLDER, f"temp_bgm_{unique_id}.mp3")
    output_local_path = os.path.join(FINAL_FOLDER, f"video_with_bgm_{unique_id}.mp4")

    try:
        # S3에서 비디오와 BGM 다운로드
        s3.download_file(S3_BUCKET_NAME, video_s3_url.split('amazonaws.com/')[-1], video_input_local_path)
        s3.download_file(S3_BUCKET_NAME, bgm_s3_url.split('amazonaws.com/')[-1], bgm_input_local_path)

        # 비디오 길이 측정 (FFmpeg 오디오 믹싱 시 BGM 길이를 맞추기 위함)
        cmd_duration = [
            'ffprobe',
            '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            video_input_local_path
        ]
        video_duration_result = subprocess.run(cmd_duration, capture_output=True, text=True, check=True)
        video_duration = float(video_duration_result.stdout.strip())

        # FFmpeg로 오디오 믹싱
        # -filter_complex: 비디오 오디오 볼륨 조절 및 BGM 믹싱
        # [0:a] 비디오의 오디오 스트림, [1:a] BGM의 오디오 스트림
        # amix=inputs=2:duration=first:dropout_transition=0: Normalize=0
        # duration=first는 비디오 길이에 맞춰 믹싱
        # anullsrc은 비디오에 오디오가 없는 경우를 대비하여 빈 오디오 스트림을 생성
        # -shortest: 비디오 길이가 BGM보다 짧을 경우, 비디오 길이에 맞춰 BGM을 자름
        cmd = [
            'ffmpeg',
            '-i', video_input_local_path,
            '-i', bgm_input_local_path,
            '-filter_complex',
            f"[0:a]volume={video_volume}[a0];[1:a]volume={bgm_volume}[a1];[a0][a1]amix=inputs=2:duration=first:dropout_transition=0[aout]",
            '-map', '0:v', # 비디오 스트림은 원본에서 가져옴
            '-map', '[aout]', # 믹싱된 오디오 스트림 사용
            '-c:v', 'copy', # 비디오는 재인코딩 없이 복사 (빠른 처리)
            '-c:a', 'aac',  # 오디오 코덱은 AAC로 인코딩 (호환성)
            '-b:a', '192k', # 오디오 비트레이트 설정
            '-shortest', # 비디오 길이에 맞춰 BGM 자르기
            output_local_path
        ]
        subprocess.run(cmd, check=True, capture_output=True)

        # 결과 비디오를 S3에 업로드
        new_s3_key = f"{FINAL_FOLDER}/{os.path.basename(output_local_path)}"
        s3.upload_file(output_local_path, S3_BUCKET_NAME, new_s3_key)
        new_s3_url = f"https://{S3_BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/{new_s3_key}"

        # DB 업데이트
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                sql = "UPDATE videos SET s3_url = %s, status = %s WHERE id = %s"
                cursor.execute(sql, (new_s3_url, 'bgm_added', video_id))
                conn.commit()
        finally:
            conn.close()

        # 로컬 파일 삭제
        os.remove(video_input_local_path)
        os.remove(bgm_input_local_path)
        os.remove(output_local_path)

        return jsonify({
            'message': 'BGM added to video successfully',
            'video_id': video_id,
            'new_s3_url': new_s3_url,
            'status': 'bgm_added'
        }), 200

    except subprocess.CalledProcessError as e:
        print(f"FFmpeg error during BGM adding: {e.stderr}")
        return jsonify({'error': 'Failed to add BGM to video (ffmpeg error)'}), 500
    except Exception as e:
        print(f"BGM adding error: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        if os.path.exists(video_input_local_path):
            os.remove(video_input_local_path)
        if os.path.exists(bgm_input_local_path):
            os.remove(bgm_input_local_path)
        if os.path.exists(output_local_path):
            os.remove(output_local_path)


# 자막 추가
@app.route('/add_subtitles_to_video', methods=['POST'])
def add_subtitles_to_video():
    data = request.json
    video_s3_url = data.get('video_s3_url')
    video_id = data.get('video_id')
    subtitles_data = data.get('subtitles') # [{'text': 'Hello', 'start': 0, 'end': 3, 'font': 'Arial.ttf', 'size': 24, 'color': '#FFFFFF', 'x': 50, 'y': 50, 'box_fill_color': '#000000@0.5'}]
    font_name = data.get('font_name', 'NanumGothic.ttf') # 기본 폰트
    font_size = data.get('font_size', 30)
    font_color = data.get('font_color', 'white')
    pos_x = data.get('pos_x', '(w-text_w)/2') # 기본 가운데 정렬
    pos_y = data.get('pos_y', 'h-th-20') # 기본 하단 정렬

    if not all([video_s3_url, video_id is not None, subtitles_data]):
        return jsonify({'error': 'Missing subtitle parameters'}), 400

    unique_id = str(uuid.uuid4())
    input_local_path = os.path.join(UPLOAD_FOLDER, f"temp_video_sub_{unique_id}.mp4")
    output_local_path = os.path.join(FINAL_FOLDER, f"video_with_subtitles_{unique_id}.mp4")

    try:
        # S3에서 비디오 다운로드
        s3.download_file(S3_BUCKET_NAME, video_s3_url.split('amazonaws.com/')[-1], input_local_path)

        # 폰트 파일 경로 확인 (로컬 캐시에서)
        font_path = os.path.join(FONT_CACHE_FOLDER, font_name)
        if not os.path.exists(font_path):
            return jsonify({'error': f"Font file not found: {font_name}. Please upload it to S3 fonts folder."}), 400

        # FFmpeg drawtext 필터 옵션 생성
        drawtext_filters = []
        for sub in subtitles_data:
            text = sub.get('text', '').replace("'", "\\'").replace(':', '\\:') # FFmpeg 필터에서 사용할 수 있도록 특수문자 이스케이프
            start_time = sub.get('start', 0)
            end_time = sub.get('end', 99999) # 무제한
            
            # 개별 자막마다 스타일 적용 (전달받은 데이터가 더 우선)
            current_font_name = sub.get('font_name', font_name)
            current_font_path = os.path.join(FONT_CACHE_FOLDER, current_font_name)
            if not os.path.exists(current_font_path):
                 print(f"Warning: Specific font {current_font_name} not found. Using default {font_name}.")
                 current_font_path = font_path

            current_font_size = sub.get('font_size', font_size)
            current_font_color = sub.get('font_color', font_color)
            current_pos_x = sub.get('pos_x', pos_x)
            current_pos_y = sub.get('pos_y', pos_y)
            box_fill_color = sub.get('box_fill_color', None) # 배경 상자 색상 (예: '#000000@0.5')
            
            filter_str = (
                f"fontfile='{current_font_path}':"
                f"text='{text}':"
                f"x={current_pos_x}:y={current_pos_y}:"
                f"fontsize={current_font_size}:fontcolor={current_font_color}:"
                f"enable='between(t,{start_time},{end_time})'"
            )
            if box_fill_color:
                filter_str += f":box=1:boxcolor={box_fill_color}:boxborderw=10" # boxborderw는 상자와 텍스트 사이의 여백

            drawtext_filters.append(filter_str)
        
        # 모든 자막 필터를 하나의 필터 문자열로 결합 (콤마로 구분)
        filter_complex_str = ",drawtext=".join(drawtext_filters)
        if filter_complex_str:
            filter_complex_str = f"drawtext={filter_complex_str}" # 첫 번째 drawtext는 앞에 붙음


        # FFmpeg로 자막 추가
        cmd = [
            'ffmpeg',
            '-i', input_local_path,
            '-vf', filter_complex_str, # 비디오 필터 적용
            '-c:a', 'copy', # 오디오는 재인코딩 없이 복사
            output_local_path
        ]
        subprocess.run(cmd, check=True, capture_output=True)


        # 결과 비디오를 S3에 업로드
        new_s3_key = f"{FINAL_FOLDER}/{os.path.basename(output_local_path)}"
        s3.upload_file(output_local_path, S3_BUCKET_NAME, new_s3_key)
        new_s3_url = f"https://{S3_BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/{new_s3_key}"

        # DB 업데이트
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                sql = "UPDATE videos SET s3_url = %s, status = %s WHERE id = %s"
                cursor.execute(sql, (new_s3_url, 'subtitled', video_id))
                conn.commit()
        finally:
            conn.close()

        # 로컬 파일 삭제
        os.remove(input_local_path)
        os.remove(output_local_path)

        return jsonify({
            'message': 'Subtitles added to video successfully',
            'video_id': video_id,
            'new_s3_url': new_s3_url,
            'status': 'subtitled'
        }), 200

    except subprocess.CalledProcessError as e:
        print(f"FFmpeg error during subtitling: {e.stderr}")
        return jsonify({'error': 'Failed to add subtitles to video (ffmpeg error)'}), 500
    except Exception as e:
        print(f"Subtitle adding error: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        if os.path.exists(input_local_path):
            os.remove(input_local_path)
        if os.path.exists(output_local_path):
            os.remove(output_local_path)


if __name__ == '__main__':
    app.run(debug=True, port=5000)