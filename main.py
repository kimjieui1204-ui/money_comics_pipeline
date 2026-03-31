import os
import sys
import json
import logging
import requests
import xml.etree.ElementTree as ET
from datetime import datetime

# 명시적 호출로 Shadowing 충돌 원천 차단
import youtube_transcript_api 
from google import genai
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def get_latest_video_transcript(channel_id):
    logging.info(f"Fetching latest video from channel ID: {channel_id} via RSS...")
    try:
        rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
        response = requests.get(rss_url)
        response.raise_for_status()
        
        root = ET.fromstring(response.content)
        ns = {'yt': 'http://www.youtube.com/xml/schemas/2015', 'atom': 'http://www.w3.org/2005/Atom'}
        
        entry = root.find('atom:entry', ns)
        if not entry:
            raise ValueError("RSS 피드에서 영상을 찾을 수 없습니다.")
            
        video_id = entry.find('yt:videoId', ns).text
        video_title = entry.find('atom:title', ns).text
        logging.info(f"Latest video ID: {video_id}, Title: {video_title}")
        
        # 💡 쇼츠나 광고 영상 등 분석할 가치가 없는 제목 필터링 (필요시 키워드 추가)
        skip_keywords = ["미에로화이바", "쇼츠", "Shorts", "예고편"]
        if any(keyword in video_title for keyword in skip_keywords):
            logging.warning(f"광고/쇼츠로 의심되는 영상입니다. 분석을 건너뜁니다: {video_title}")
            return video_title, video_id, "SKIP"
        
        logging.info("Downloading transcript in Korean...")
        transcript_list = youtube_transcript_api.YouTubeTranscriptApi.get_transcript(video_id, languages=['ko', 'ko-KR'])
        transcript_text = " ".join([t['text'] for t in transcript_list])
        return video_title, video_id, transcript_text
        
    except youtube_transcript_api.TranscriptsDisabled:
        logging.error("해당 영상에 자막이 비활성화되어 있습니다 (주로 쇼츠/광고).")
        return video_title, video_id, "NO_TRANSCRIPT"
    except Exception as e:
        logging.error(f"Failed to get transcript. Error: {e}")
        return None, None, None

def analyze_transcript(transcript_text):
    logging.info("Analyzing transcript with the NEW google-genai SDK...")
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logging.error("GEMINI_API_KEY is not set.")
        return None
        
    try:
        client = genai.Client(api_key=api_key)
        prompt = f"""
        다음은 유튜브 채널 '머니코믹스'의 최신 영상 스크립트입니다. 이 내용을 바탕으로 심층 분석 리포트를 작성해주세요.

        [스크립트 내용]
        {transcript_text}

        [요청 사항]
        1. 금리, 노동 지표 등 매크로 요인이 시장에 미치는 영향을 초보자도 직관적으로 이해할 수 있도록 반드시 **비유를 들어서** 설명해주세요.
        2. 스크립트에서 다루는 타겟 기업의 히스토리와 향후 전망을 피상적이지 않게 **매우 깊고 날카롭게(딥하게) 분석**해주세요.
        3. EPS, ROE 같은 기본적인 주식/금융 용어에 대한 설명은 지면 낭비이므로 **모두 생략**하세요.
        4. 리포트는 요점과 문단을 명확히 나누고 글머리 기호를 활용하여 가독성이 뛰어난 산출물로 작성해주세요.
        """
        response = client.models.generate_content(
            model='gemini-1.5-pro',
            contents=prompt,
        )
        return response.text
    except Exception as e:
        logging.error(f"Gemini API error: {e}")
        return None

def create_google_doc(title, content):
    logging.info("Creating Google Doc...")
    try:
        creds_json_str = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
        if not creds_json_str:
            raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON is not set.")
            
        creds_info = json.loads(creds_json_str)
        scopes = ['https://www.googleapis.com/auth/documents', 'https://www.googleapis.com/auth/drive']
        creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
        
        docs_service = build('docs', 'v1', credentials=creds)
        drive_service = build('drive', 'v3', credentials=creds)
        
        doc_title = f"[머니코믹스 분석] {title} - {datetime.now().strftime('%Y-%m-%d')}"
        document = docs_service.documents().create(body={'title': doc_title}).execute()
        document_id = document.get('documentId')
        
        requests_body = [{'insertText': {'location': {'index': 1}, 'text': content}}]
        docs_service.documents().batchUpdate(documentId=document_id, body={'requests': requests_body}).execute()
        
        drive_service.permissions().create(
            fileId=document_id,
            body={'type': 'anyone', 'role': 'reader'},
            fields='id'
        ).execute()
        
        doc_url = f"https://docs.google.com/document/d/{document_id}/edit"
        logging.info(f"Google Doc created successfully: {doc_url}")
        return doc_url
    except Exception as e:
        logging.error(f"Google Docs error: {e}")
        return None

def send_telegram_message(message):
    logging.info("Sending message to Telegram...")
    try:
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        
        if not bot_token or not chat_id:
            raise ValueError("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is not set.")
            
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        requests.post(url, json={"chat_id": chat_id, "text": message}).raise_for_status()
        logging.info("Telegram message sent successfully.")
    except Exception as e:
        logging.error(f"Telegram API error: {e}")

def main():
    raw_channel_id = os.environ.get("YOUTUBE_CHANNEL_ID")
    if not raw_channel_id or raw_channel_id.strip() == "":
        channel_id = "UCJo6G1u0e_-wS-JQn3T-zEw" # 머니코믹스 기본 ID
    else:
        channel_id = raw_channel_id.strip()
    
    title, video_id, transcript = get_latest_video_transcript(channel_id)
    
    # 예외 상황 처리 (광고이거나 자막이 없는 경우 텔레그램 알림만 보내고 정상 종료)
    if transcript in ["SKIP", "NO_TRANSCRIPT"]:
        msg = f"ℹ️ 오늘 올라온 영상 '{title}'은 분석 대상(자막 없음/광고)이 아니어 스킵했습니다."
        send_telegram_message(msg)
        sys.exit(0) # 에러가 아니므로 정상 종료 처리
        
    if not transcript:
        logging.error("Failed to fetch transcript. Exiting pipeline.")
        sys.exit(1)
        
    analysis = analyze_transcript(transcript)
    if not analysis:
        logging.error("Failed to generate analysis. Exiting pipeline.")
        sys.exit(1)
        
    doc_url = create_google_doc(title, analysis)
    if not doc_url:
        logging.error("Failed to create Google Doc. Exiting pipeline.")
        sys.exit(1)
        
    msg = f"🚀 머니코믹스 최신 분석 리포트!\n\n📺 영상: {title}\n🔗 유튜브: https://youtu.be/{video_id}\n\n📄 분석 리포트(Docs): {doc_url}"
    send_telegram_message(msg)

if __name__ == "__main__":
    main()

if __name__ == "__main__":
    main()
