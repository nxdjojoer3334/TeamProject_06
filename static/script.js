// 전역 변수
let currentVideoId = null;
let currentVideoS3Url = null;
let currentVideoDuration = 0;
let userVideos = []; // 사용자 비디오 목록 저장
let bgmList = []; // BGM 목록 저장

// DOM 로드 완료 시 초기화 함수 호출
document.addEventListener('DOMContentLoaded', () => {
    // 탭 초기화
    openTab(null, 'upload-tab');
    // 비디오 목록 로드
    loadUserVideos();
    // BGM 목록 로드
    loadBGMList();
    // 폰트 목록 로드
    loadAvailableFonts();

    // 볼륨 슬라이더 값 표시 업데이트
    document.getElementById('bgmVolume').addEventListener('input', (event) => {
        document.getElementById('bgmVolumeValue').textContent = event.target.value;
    });
    document.getElementById('videoVolume').addEventListener('input', (event) => {
        document.getElementById('videoVolumeValue').textContent = event.target.value;
    });

    // 자막 배경 투명도 슬라이더 값 표시 업데이트
    document.getElementById('subBoxOpacity').addEventListener('input', (event) => {
        const opacity = parseFloat(event.target.value);
        const color = document.getElementById('subBoxColor').value;
        // 색상 코드에 투명도 추가 (예: #RRGGBB@A)
        const rgbaColor = `${color}${Math.round(opacity * 255).toString(16).padStart(2, '0')}`;
        // 실제로는 FFmpeg 필터에서 @0.5 형태로 사용하므로, UI에만 보여주는 용도
    });
});

// 탭 전환 함수
function openTab(evt, tabName) {
    let i, tabContent, tabButtons;
    tabContent = document.getElementsByClassName("tab-content");
    for (i = 0; i < tabContent.length; i++) {
        tabContent[i].style.display = "none";
    }
    tabButtons = document.getElementsByClassName("tab-button");
    for (i = 0; i < tabButtons.length; i++) {
        tabButtons[i].className = tabButtons[i].className.replace(" active", "");
    }
    document.getElementById(tabName).style.display = "block";
    if (evt) {
        evt.currentTarget.className += " active";
    } else {
        // DOMContentLoaded에서 호출 시 첫 탭 활성화
        document.querySelector(`.tab-button[onclick*='${tabName}']`).className += " active";
    }
}

// 메시지 표시 함수
function displayMessage(elementId, message, type) {
    const messageElement = document.getElementById(elementId);
    messageElement.textContent = message;
    messageElement.className = `message ${type}`;
    // 메시지 3초 후 사라지게
    setTimeout(() => {
        messageElement.textContent = '';
        messageElement.className = 'message';
    }, 5000);
}

// 시간 포맷팅 함수 (초 -> HH:MM:SS)
function formatTime(seconds) {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    return [h, m, s]
        .map(v => v < 10 ? "0" + v : v)
        .join(":");
}

// 비디오 미리보기 업데이트 함수
const videoPlayer = document.getElementById('videoPlayer');
videoPlayer.addEventListener('timeupdate', () => {
    document.getElementById('currentTime').textContent = formatTime(videoPlayer.currentTime);
});
videoPlayer.addEventListener('loadedmetadata', () => {
    currentVideoDuration = videoPlayer.duration;
    document.getElementById('totalDuration').textContent = formatTime(videoPlayer.duration);
    document.getElementById('trimEnd').value = videoPlayer.duration.toFixed(1); // 트리밍 종료 시간 기본 설정
});

// 사용자 비디오 목록 로드 (select 박스 채우기)
async function loadUserVideos() {
    try {
        const response = await fetch('/get_user_videos');
        const data = await response.json();
        if (data.error) {
            displayMessage('videoSelectMessage', `비디오 로드 오류: ${data.error}`, 'error');
            return;
        }
        userVideos = data; // 전역 변수에 저장
        const selectElement = document.getElementById('userVideosSelect');
        selectElement.innerHTML = '<option value="">비디오를 선택해주세요</option>'; // 초기화
        userVideos.forEach(video => {
            const option = document.createElement('option');
            option.value = video.s3_url;
            option.dataset.videoId = video.id; // video ID 저장
            option.dataset.duration = video.duration; // 비디오 길이 저장
            option.textContent = `${video.original_filename} (${formatTime(video.duration)}) - ${video.status}`;
            selectElement.appendChild(option);
        });
    } catch (error) {
        displayMessage('videoSelectMessage', `비디오 로드 중 네트워크 오류: ${error}`, 'error');
    }
}

// 선택된 비디오 플레이어에 로드
function loadSelectedVideo() {
    const selectElement = document.getElementById('userVideosSelect');
    const selectedOption = selectElement.options[selectElement.selectedIndex];
    
    if (selectedOption.value) {
        currentVideoS3Url = selectedOption.value;
        currentVideoId = selectedOption.dataset.videoId;
        currentVideoDuration = parseFloat(selectedOption.dataset.duration);

        videoPlayer.src = currentVideoS3Url;
        document.getElementById('trimStart').value = 0;
        document.getElementById('trimEnd').value = currentVideoDuration.toFixed(1);
        displayMessage('videoSelectMessage', '비디오가 로드되었습니다.', 'success');
    } else {
        videoPlayer.src = ''; // 비디오 플레이어 초기화
        currentVideoId = null;
        currentVideoS3Url = null;
        currentVideoDuration = 0;
        document.getElementById('trimStart').value = 0;
        document.getElementById('trimEnd').value = 0;
        document.getElementById('totalDuration').textContent = '00:00:00';
        displayMessage('videoSelectMessage', '비디오 선택이 해제되었습니다.', 'info');
    }
    updateFinalVideoLink(''); // 최종 비디오 링크 초기화
}

// 비디오 업로드
async function uploadVideo() {
    const fileInput = document.getElementById('videoFileInput');
    const file = fileInput.files[0];

    if (!file) {
        displayMessage('uploadMessage', '업로드할 비디오 파일을 선택해주세요.', 'error');
        return;
    }

    const formData = new FormData();
    formData.append('video', file);

    displayMessage('uploadMessage', '비디오 업로드 중...', 'info');

    try {
        const response = await fetch('/upload_video', {
            method: 'POST',
            body: formData
        });
        const data = await response.json();

        if (data.error) {
            displayMessage('uploadMessage', `업로드 실패: ${data.error}`, 'error');
        } else {
            displayMessage('uploadMessage', data.message, 'success');
            // 업로드 성공 후 비디오 목록 새로 고침
            loadUserVideos();
            // 업로드된 비디오를 자동으로 선택 (선택 사항)
            // const selectElement = document.getElementById('userVideosSelect');
            // selectElement.value = data.s3_url;
            // loadSelectedVideo();
        }
    } catch (error) {
        displayMessage('uploadMessage', `네트워크 오류: ${error}`, 'error');
    }
}

// 비디오 트리밍
async function trimVideo() {
    if (!currentVideoId || !currentVideoS3Url) {
        displayMessage('trimMessage', '먼저 편집할 비디오를 선택해주세요.', 'error');
        return;
    }

    const trimStart = parseFloat(document.getElementById('trimStart').value);
    const trimEnd = parseFloat(document.getElementById('trimEnd').value);

    if (isNaN(trimStart) || isNaN(trimEnd) || trimStart < 0 || trimEnd <= trimStart || trimEnd > currentVideoDuration) {
        displayMessage('trimMessage', '유효한 트리밍 시간을 입력해주세요.', 'error');
        return;
    }

    displayMessage('trimMessage', '비디오 트리밍 중...', 'info');

    try {
        const response = await fetch('/trim_video', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                video_id: currentVideoId,
                video_s3_url: currentVideoS3Url,
                start_time: trimStart,
                end_time: trimEnd
            })
        });
        const data = await response.json();

        if (data.error) {
            displayMessage('trimMessage', `트리밍 실패: ${data.error}`, 'error');
        } else {
            displayMessage('trimMessage', data.message, 'success');
            currentVideoS3Url = data.new_s3_url; // 새 S3 URL로 업데이트
            videoPlayer.src = currentVideoS3Url; // 플레이어에 새 비디오 로드
            loadUserVideos(); // 비디오 목록 새로 고침
            updateFinalVideoLink(data.new_s3_url); // 최종 링크 업데이트
        }
    } catch (error) {
        displayMessage('trimMessage', `네트워크 오류: ${error}`, 'error');
    }
}

// BGM 목록 로드 (select 박스 및 BGM 관리 탭)
async function loadBGMList() {
    try {
        const response = await fetch('/get_bgm_list');
        const data = await response.json();
        if (data.error) {
            displayMessage('bgmDownloadMessage', `BGM 목록 로드 오류: ${data.error}`, 'error');
            return;
        }
        bgmList = data; // 전역 변수에 저장

        // BGM 추가 탭의 Select 박스 채우기
        const bgmSelectElement = document.getElementById('bgmSelect');
        bgmSelectElement.innerHTML = '<option value="">BGM을 선택해주세요</option>';
        bgmList.forEach(bgm => {
            const option = document.createElement('option');
            option.value = bgm.s3_url;
            option.dataset.bgmFilename = bgm.filename;
            option.textContent = `${bgm.title} (${formatTime(bgm.duration || 0)})`;
            bgmSelectElement.appendChild(option);
        });

        // BGM 관리 탭의 목록 채우기
        const downloadedBGMListElement = document.getElementById('downloadedBBMList');
        downloadedBGMListElement.innerHTML = ''; // 초기화
        if (bgmList.length === 0) {
            const li = document.createElement('li');
            li.textContent = '다운로드된 BGM이 없습니다.';
            downloadedBGMListElement.appendChild(li);
        } else {
            bgmList.forEach(bgm => {
                const li = document.createElement('li');
                li.innerHTML = `<span>${bgm.title}</span> (${formatTime(bgm.duration || 0)}) <a href="${bgm.s3_url}" target="_blank">다운로드</a>`;
                downloadedBGMListElement.appendChild(li);
            });
        }

    } catch (error) {
        displayMessage('bgmDownloadMessage', `BGM 목록 로드 중 네트워크 오류: ${error}`, 'error');
    }
}

// YouTube BGM 다운로드
async function downloadBGMFromYouTube() {
    const youtubeUrl = document.getElementById('youtubeUrlInput').value;
    if (!youtubeUrl) {
        displayMessage('bgmDownloadMessage', 'YouTube URL을 입력해주세요.', 'error');
        return;
    }

    displayMessage('bgmDownloadMessage', 'BGM 다운로드 중...', 'info');

    try {
        const response = await fetch('/download_bgm_from_youtube', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ youtube_url: youtubeUrl })
        });
        const data = await response.json();

        if (data.error) {
            displayMessage('bgmDownloadMessage', `BGM 다운로드 실패: ${data.error}`, 'error');
        } else {
            displayMessage('bgmDownloadMessage', data.message, 'success');
            document.getElementById('youtubeUrlInput').value = ''; // 입력 필드 초기화
            loadBGMList(); // BGM 목록 새로 고침
        }
    } catch (error) {
        displayMessage('bgmDownloadMessage', `네트워크 오류: ${error}`, 'error');
    }
}

// 비디오에 BGM 추가
async function addBGMToVideo() {
    if (!currentVideoId || !currentVideoS3Url) {
        displayMessage('bgmMessage', '먼저 편집할 비디오를 선택해주세요.', 'error');
        return;
    }

    const bgmSelect = document.getElementById('bgmSelect');
    const selectedBGMUrl = bgmSelect.value;
    if (!selectedBGMUrl) {
        displayMessage('bgmMessage', '추가할 BGM을 선택해주세요.', 'error');
        return;
    }

    const bgmVolume = parseFloat(document.getElementById('bgmVolume').value);
    const videoVolume = parseFloat(document.getElementById('videoVolume').value);

    displayMessage('bgmMessage', 'BGM을 비디오에 추가하는 중...', 'info');

    try {
        const response = await fetch('/add_bgm_to_video', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                video_id: currentVideoId,
                video_s3_url: currentVideoS3Url,
                bgm_s3_url: selectedBGMUrl,
                bgm_volume: bgmVolume,
                video_volume: videoVolume
            })
        });
        const data = await response.json();

        if (data.error) {
            displayMessage('bgmMessage', `BGM 추가 실패: ${data.error}`, 'error');
        } else {
            displayMessage('bgmMessage', data.message, 'success');
            currentVideoS3Url = data.new_s3_url; // 새 S3 URL로 업데이트
            videoPlayer.src = currentVideoS3Url; // 플레이어에 새 비디오 로드
            loadUserVideos(); // 비디오 목록 새로 고침
            updateFinalVideoLink(data.new_s3_url); // 최종 링크 업데이트
        }
    } catch (error) {
        displayMessage('bgmMessage', `네트워크 오류: ${error}`, 'error');
    }
}

// 사용 가능한 폰트 목록 로드
async function loadAvailableFonts() {
    try {
        const response = await fetch('/get_available_fonts');
        const data = await response.json();
        if (data.error) {
            console.error(`폰트 목록 로드 오류: ${data.error}`);
            return;
        }
        const fontSelect = document.getElementById('fontSelect');
        fontSelect.innerHTML = ''; // 초기화
        data.forEach(font => {
            const option = document.createElement('option');
            option.value = font;
            option.textContent = font;
            fontSelect.appendChild(option);
        });
        // 기본 폰트 선택 (예: NanumGothic.ttf)
        if (fontSelect.querySelector('option[value="NanumGothic.ttf"]')) {
            fontSelect.value = 'NanumGothic.ttf';
        }
    } catch (error) {
        console.error(`폰트 목록 로드 중 네트워크 오류: ${error}`);
    }
}

// 자막 입력 필드 추가
function addSubtitleInput() {
    const container = document.getElementById('subtitle-input-container');
    const newEntry = document.createElement('div');
    newEntry.className = 'subtitle-entry';
    newEntry.innerHTML = `
        <input type="text" placeholder="자막 내용" class="subtitle-text">
        <input type="number" placeholder="시작(초)" step="0.1" class="subtitle-start">
        <input type="number" placeholder="종료(초)" step="0.1" class="subtitle-end">
        <button onclick="removeSubtitleInput(this)">삭제</button>
    `;
    container.appendChild(newEntry);
}

// 자막 입력 필드 삭제
function removeSubtitleInput(buttonElement) {
    buttonElement.closest('.subtitle-entry').remove();
}

// 비디오에 자막 추가
async function addSubtitlesToVideo() {
    if (!currentVideoId || !currentVideoS3Url) {
        displayMessage('subtitleMessage', '먼저 편집할 비디오를 선택해주세요.', 'error');
        return;
    }

    const subtitleEntries = document.querySelectorAll('.subtitle-entry');
    const subtitlesData = [];

    subtitleEntries.forEach(entry => {
        const text = entry.querySelector('.subtitle-text').value;
        const start = parseFloat(entry.querySelector('.subtitle-start').value);
        const end = parseFloat(entry.querySelector('.subtitle-end').value);

        if (text && !isNaN(start) && !isNaN(end) && end > start) {
            subtitlesData.push({ text, start, end });
        } else if (text) { // 내용이 있지만 시간이 유효하지 않은 경우
            displayMessage('subtitleMessage', '자막 시간(시작/종료)을 올바르게 입력해주세요.', 'error');
            return; // 유효하지 않은 자막이 있으면 함수 종료
        }
    });

    if (subtitlesData.length === 0) {
        displayMessage('subtitleMessage', '추가할 자막 내용을 입력해주세요.', 'error');
        return;
    }

    // 전역 자막 스타일 설정
    const fontName = document.getElementById('fontSelect').value;
    const fontSize = parseInt(document.getElementById('fontSize').value);
    const fontColor = document.getElementById('fontColor').value;
    const posX = document.getElementById('subPosX').value;
    const posY = document.getElementById('subPosY').value;
    const subBoxColorRaw = document.getElementById('subBoxColor').value;
    const subBoxOpacity = parseFloat(document.getElementById('subBoxOpacity').value);
    const subBoxColor = `${subBoxColorRaw}@${subBoxOpacity}`; // FFmpeg 형식으로 변환

    displayMessage('subtitleMessage', '자막을 비디오에 추가하는 중...', 'info');

    try {
        const response = await fetch('/add_subtitles_to_video', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                video_id: currentVideoId,
                video_s3_url: currentVideoS3Url,
                subtitles: subtitlesData,
                font_name: fontName,
                font_size: fontSize,
                font_color: fontColor,
                pos_x: posX,
                pos_y: posY,
                box_fill_color: subBoxColor
            })
        });
        const data = await response.json();

        if (data.error) {
            displayMessage('subtitleMessage', `자막 추가 실패: ${data.error}`, 'error');
        } else {
            displayMessage('subtitleMessage', data.message, 'success');
            currentVideoS3Url = data.new_s3_url; // 새 S3 URL로 업데이트
            videoPlayer.src = currentVideoS3Url; // 플레이어에 새 비디오 로드
            loadUserVideos(); // 비디오 목록 새로 고침
            updateFinalVideoLink(data.new_s3_url); // 최종 링크 업데이트
        }
    } catch (error) {
        displayMessage('subtitleMessage', `네트워크 오류: ${error}`, 'error');
    }
}

// 최종 비디오 다운로드 링크 업데이트
function updateFinalVideoLink(url) {
    const linkElement = document.getElementById('finalVideoLink').querySelector('a');
    if (url) {
        linkElement.href = url;
        linkElement.style.display = 'block';
        linkElement.textContent = '최종 비디오 다운로드';
    } else {
        linkElement.href = '#';
        linkElement.style.display = 'none';
        linkElement.textContent = '';
    }
}