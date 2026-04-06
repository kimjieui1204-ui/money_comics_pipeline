import os
import sys
import json
import logging
import requests
import xml.etree.ElementTree as ET
from datetime import datetime

# 라이브러리 임포트 (클래스 직접 참조 방식)
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound
from google import genai
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# 로깅 설정 (GitHub Action 로그에서 가독성 확보)
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - [%(levelname)s] - %(message)s'
)

def get_latest_video_transcript(channel_id):
    """RSS 피드를 통해 최신 영상을 찾고 자막을 추출합니다."""
    logging.info(f"Checking YouTube RSS for channel: {channel_id}")
    try:
        rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
        response = requests.get(rss_url, timeout=10)
        response.raise_for_status()
        
        root = ET.fromstring(response.content)
        ns = {'yt': 'http://www.youtube.com/xml/schemas/2015', 'atom': 'http://www.w3.org/2005/Atom'}
        
        entry = root.find('atom:entry', ns)
        if not entry:
            raise ValueError("RSS 피드 데이터가 비어있습니다.")
            
        video_id = entry.find('yt:videoId', ns).text
        video_title = entry.find('atom:title', ns).text
        logging.info(f"Target Video Found -> ID: {video_id} / Title: {video_title}")
        
        # 분석 가치가 떨어지는 노이즈 필터링
        skip_keywords = ["미에로화이바", "쇼츠", "Shorts", "예고편", "AD", "광고"]
        if any(keyword in video_title for keyword in skip_keywords):
            logging.warning("분석 제외 대상(광고/쇼츠) 키워드가 감지되었습니다.")
            return video_title, video_id, "SKIP"
        
        logging.info("Attempting to download Korean transcript...")
        # 핵심: 클래스 메서드 직접 호출 (Error Patch)
        transcript_list = YouTubeTranscriptApi.get_transcript(video_id, languages=['ko', 'ko-KR'])
        transcript_text = " ".join([t['text'] for t in transcript_list])
        
        return video_title, video_id, transcript_text
        
    except (TranscriptsDisabled, NoTranscriptFound):
        logging.error(f"Transcript unavailable for video {video_id}. (Possible Shorts or disabled)")
        return video_title, video_id, "NO_TRANSCRIPT"
    except Exception as e:
        logging.error(f"Critical error in fetching transcript: {str(e)}")
        return None, None, None

def analyze_transcript(transcript_text):
    """Gemini 1.5 Pro를 활용하여 투자 인사이트를 도출합니다."""
    logging.info("Initializing AI Analysis (Gemini 1.5 Pro)...")
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logging.error("GEMINI_API_KEY environment variable is missing.")
        return None
        
    try:
        client = genai.Client(api_key=api_key)
        
        # 페르소나 및 분석 지침 강화
        prompt = f"""
        당신은 상위 1% 퀀트 분석가이자 거시경제 전문가입니다. 
        다음 유튜브 스크립트를 바탕으로 전문적인 투자 리포트를 작성하세요.

        [스크립트 내용]
        {transcript_text}

        [작성 원칙]
        1. 매크로 지표(금리, 고용 등)가 시장에 주는 영향은 직관적인 비유(예: 엔진 오일, 가속 페달 등)를 섞어 설명할 것.
        2. 타겟 기업에 대해 단순 요약이 아닌, 히스토리 기반의 날카로운 '딥 다이브' 분석을 제공할 것.
        3. EPS, ROE 등 당신과 내가 이미 알고 있는 기초 용어 설명은 절대 하지 말 것. (지면 낭비 금지)
        4. 혁신적이고 실질적인 관점에서 향후 대응 전략을 제시할 것.
        5. 마크다운 형식을 활용하여 가독성을 극대화할 것.
        """
        
        response = client.models.generate_content(
            model='gemini-1.5-pro',
            contents=prompt,
        )
        return response.text
    except Exception as e:
        logging.error(f"Gemini API Analysis failed: {str(e)}")
        return None

def create_google_doc(title, content):
    """결과물을 구글 문서로 아카이빙합니다."""
    logging.info("Exporting analysis to Google Docs...")
    try:
        creds_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
        if not creds_json:
            raise ValueError("Google Service Account JSON is missing.")
            
        creds_info = json.loads(creds_json)
        scopes = ['https://www.googleapis.com/auth/documents', 'https://www.googleapis.com/auth/drive']
        creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
        
        docs_service = build('docs', 'v1', credentials=creds)
        drive_service = build('drive', 'v3', credentials=creds)
        
        doc_name = f"[Deep-Analysis] {title} ({datetime.now().strftime('%y%m%d')})"
        doc = docs_service.documents().create(body={'title': doc_name}).execute()
        doc_id = doc.get('documentId')
        
        # 텍스트 삽입
        requests_body = [{'insertText': {'location': {'index': 1}, 'text': content}}]
        docs_service.documents().batchUpdate(documentId=doc_id, body={'requests': requests_body}).execute()
        
        # 권한 설정 (누구나 읽기 가능)
        drive_service.permissions().create(
            fileId=doc_id,
            body={'type': 'anyone', 'role': 'reader'}
        ).execute()
        
        return f"https://docs.google.com/document/d/{doc_id}/edit"
    except Exception as e:
        logging.error(f"Google Docs Integration Error: {str(e)}")
        return None

def send_telegram_message(message):
    """최종 결과 알림."""
    try:
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        if not (token and chat_id): return
        
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.post(url, json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}, timeout=10)
    except Exception as e:
        logging.error(f"Telegram notification failed: {str(e)}")

def main():
    # 채널 ID 로드 (기본값: 머니코믹스)
    channel_id = os.environ.get("YOUTUBE_CHANNEL_ID", "UCJo6G1u0e_-wS-JQn3T-zEw").strip()
    
    title, video_id, transcript = get_latest_video_transcript(channel_id)
    
    if transcript in ["SKIP", "NO_TRANSCRIPT"]:
        msg = f"⚠️ *스킵 알림*\n영상: {title}\n사유: 분석 대상이 아니거나 자막이 없습니다."
        send_telegram_message(msg)
        sys.exit(0)
        
    if not transcript:
        logging.error("Pipeline Terminated: Transcript acquisition failed.")
        sys.exit(1)
        
    analysis = analyze_transcript(transcript)
    if not analysis:
        logging.error("Pipeline Terminated: AI Analysis failed.")
        sys.exit(1)
        
    doc_url = create_google_doc(title, analysis)
    if not doc_url:
        logging.error("Pipeline Terminated: Document creation failed.")
        sys.exit(1)
        
    # 최종 리포트 전송
    report_msg = (
        f"🚀 *머니코믹스 분석 완료*\n\n"
        f"📌 *주제:* {title}\n"
        f"🔗 [유튜브 시청하기](https://youtu.be/{video_id})\n"
        f"📄 [딥 분석 리포트 확인]({doc_url})"
    )
    send_telegram_message(report_msg)
    logging.info("### Pipeline Successfully Completed ###")

if __name__ == "__main__":
    main()
