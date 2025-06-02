from flask import Flask, request, render_template, redirect, url_for, send_from_directory
import boto3
import os
import uuid
import subprocess
from werkzeug.utils import secure_filename
import logging

app = Flask(__name__)

# --- 설정 ---
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['TRIMMED_FOLDER'] = 'trimmed'

load_dotenv()

# 2. 로깅 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
app.logger.setLevel(logging.INFO)

# 3. AWS 및 S3 설정
S3_BUCKET = 'aws-teamproject'
S3_REGION = 'ap-northeast-2'

S3_ACCESS_KEY = os.getenv('AWS_ACCESS_KEY_ID')
S3_SECRET_KEY = os.getenv('AWS_SECRET_ACCESS_KEY')

if S3_ACCESS_KEY and S3_SECRET_KEY:
    s3 = boto3.client(
        's3',
        region_name=S3_REGION,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY
    )
    app.logger.info("S3 client initialized with credentials from .env file.")
else:
    s3 = boto3.client('s3', region_name=S3_REGION)
    app.logger.info("S3 client initialized without explicit credentials (using IAM role or environment variables).")

# 4. 업로드 폴더 생성
os.makedirs(app.config.get('UPLOAD_FOLDER', 'uploads'), exist_ok=True)
os.makedirs(app.config.get('TRIMMED_FOLDER', 'trimmed'), exist_ok=True)
@app.route('/')
def index():
    return render_template('upload.html')

@app.route('/upload', methods=['POST'])
def upload():
    if 'video' not in request.files:
        app.logger.warning("No video file part in request.")
        return "업로드된 파일이 없습니다.", 400

    file = request.files['video']
    if file.filename == '':
        app.logger.warning("No file selected for upload.")
        return "선택된 파일이 없습니다.", 400

    if file:
        filename = secure_filename(file.filename)
        file_id = str(uuid.uuid4())
        saved_name = f"{file_id}_{filename}"
        path = os.path.join(app.config['UPLOAD_FOLDER'], saved_name)

        try:
            file.save(path)
            app.logger.info(f"File '{saved_name}' uploaded successfully to '{path}'.")
            return redirect(url_for('trim', filename=saved_name))
        except Exception as e:
            app.logger.error(f"Error saving uploaded file '{saved_name}': {e}", exc_info=True)
            return "파일 저장 중 오류가 발생했습니다.", 500

    return "알 수 없는 업로드 오류입니다.", 500

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    app.logger.info(f"Serving uploaded file: {filename} from {app.config['UPLOAD_FOLDER']}")
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/trim')
def trim():
    filename = request.args.get('filename')
    if not filename:
        app.logger.warning("Trim page requested without filename.")
        return "잘라낼 영상 파일명이 지정되지 않았습니다.", 400

    video_url = url_for('uploaded_file', filename=filename)
    app.logger.info(f"Trim page for '{filename}', video URL: {video_url}")
    return render_template('trim.html', filename=filename, video_url=video_url)

@app.route('/trim_video', methods=['POST'])
def trim_video():
    filename = request.form.get('filename')
    start_time_str = request.form.get('start_time')
    end_time_str = request.form.get('end_time')

    if not all([filename, start_time_str, end_time_str]):
        app.logger.warning(f"Trim video request missing data. Filename: {filename}, Start: {start_time_str}, End: {end_time_str}")
        return "필수 정보(파일명, 시작 시간, 종료 시간)가 누락되었습니다.", 400

    try:
        start_time = float(start_time_str)
        end_time = float(end_time_str)
        if start_time < 0 or end_time <= 0 or end_time <= start_time:
            app.logger.warning(f"Invalid time range. Start: {start_time}, End: {end_time}")
            return "시간 범위가 올바르지 않습니다.", 400
        duration = end_time - start_time
    except ValueError:
        app.logger.warning(f"Invalid time format. Start: {start_time_str}, End: {end_time_str}")
        return "시간 형식이 올바르지 않습니다 (숫자여야 합니다).", 400

    input_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    trimmed_filename_base = f"trimmed_{filename}"
    output_path = os.path.join(app.config['TRIMMED_FOLDER'], trimmed_filename_base)
    s3_object_key = f"trimmed/{trimmed_filename_base}"

    if not os.path.exists(input_path):
        app.logger.error(f"Input file for trimming not found: {input_path}")
        return "원본 영상을 찾을 수 없습니다. 다시 업로드해주세요.", 404

    FFMPEG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bin', 'ffmpeg.exe')
    app.logger.info(f"FFMPEG_PATH calculated as: {FFMPEG_PATH}")
    cmd = [
        FFMPEG_PATH,
        '-i', input_path,
        '-ss', str(start_time),
        '-t', str(duration),
        '-c:v', 'libx264',
        '-c:a', 'aac',
        '-y',
        output_path
    ]
    app.logger.info(f"Executing FFmpeg command: {' '.join(cmd)}")

    try:
        process_result = subprocess.run(cmd, 
                                        check=True, 
                                        capture_output=True, 
                                        text=True,
                                        encoding='utf-8',
                                        errors='replace')

        stdout_preview = (process_result.stdout or "")[:200]
        stderr_preview = (process_result.stderr or "")[:200]
        app.logger.info(
            f"FFmpeg processing successful for '{filename}'. "
            f"STDOUT: {stdout_preview}... STDERR: {stderr_preview}..."
        )

    except FileNotFoundError:
        app.logger.error("FFmpeg command not found. Ensure FFmpeg is installed and in PATH.")
        return "서버 내부 오류: FFmpeg를 실행할 수 없습니다. 관리자에게 문의하세요.", 500

    except subprocess.CalledProcessError as e:
        app.logger.error(f"FFmpeg failed for '{filename}'. Return code: {e.returncode}", exc_info=True)
        app.logger.error(f"FFmpeg STDOUT: {e.stdout}")
        app.logger.error(f"FFmpeg STDERR: {e.stderr}")
        return f"영상 처리 중 오류 발생 (FFmpeg): {e.stderr}", 500

    try:
        s3.upload_file(
            output_path,
            S3_BUCKET,
            s3_object_key,
            ExtraArgs={'ContentType': 'video/mp4'}
        )
        app.logger.info(f"Trimmed video '{s3_object_key}' uploaded to S3 bucket '{S3_BUCKET}'.")
    except Exception as e:
        app.logger.error(f"S3 upload failed for '{s3_object_key}': {e}", exc_info=True)
        return "S3 업로드 중 오류가 발생했습니다.", 500
    finally:
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
                app.logger.info(f"Cleaned up local trimmed file: {output_path}")
            except OSError as e:
                app.logger.error(f"Error removing local trimmed file '{output_path}': {e}", exc_info=True)

    s3_url = f"https://{S3_BUCKET}.s3.{S3_REGION}.amazonaws.com/{s3_object_key}"
    return render_template('result.html', video_url=s3_url)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)