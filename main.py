import os
import sys
import json
import logging
import requests
import xml.etree.ElementTree as ET
from datetime import datetime

from youtube_transcript_api import YouTubeTranscriptApi
import google.generativeai as genai
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def get_latest_video_transcript(channel_id):
    logging.info(f"Fetching latest video from channel ID: {channel_id} via RSS...")
    try:
        # IP 차단을 우회하는 가장 우아한 방식: YouTube RSS 피드 직접 타격
        rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
        response = requests.get(rss_url)
        response.raise_for_status()
        
        root = ET.fromstring(response.content)
        # XML 네임스페이스 매핑
        ns = {'yt': 'http://www.youtube.com/xml/schemas/2015', 'atom': 'http://www.w3.org/2005/Atom'}
        
        # 가장 최근 영상(첫 번째 entry) 추출
        entry = root.find('atom:entry', ns)
        if not entry:
            raise ValueError("RSS 피드에서 영상을 찾을 수 없습니다.")
            
        video_id = entry.find('yt:videoId', ns).text
        video_title = entry.find('atom:title', ns).text
        logging.info(f"Latest video ID: {video_id}, Title: {video_title}")
        
        logging.info("Downloading transcript in Korean...")
        # 'ko' 뿐만 아니라 'ko-KR'도 허용하도록 유연성 확보
        transcript_list = YouTubeTranscriptApi.get_transcript(video_id, languages=['ko', 'ko-KR'])
        transcript_text = " ".join([t['text'] for t in transcript_list])
        return video_title, video_id, transcript_text
        
    except Exception as e:
        logging.error(f"Failed to get transcript. Error: {e}")
        return None, None, None

def analyze_transcript(transcript_text):
    logging.info("Analyzing transcript with Gemini 1.5 Pro API...")
    if "GEMINI_API_KEY" not in os.environ:
        logging.error("GEMINI_API_KEY is not set.")
        return None
        
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    
    # 모델명은 구동 안정성을 위해 현업 주력인 1.5 Pro로 고정
    model = genai.GenerativeModel('gemini-1.5-pro')
    
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
    try:
        response = model.generate_content(prompt)
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
    # 이제 핸들이 아니라 절대 변하지 않는 채널 ID를 타겟팅함.
    channel_id = os.environ.get("YOUTUBE_CHANNEL_ID", "UCJo6G1u0e_-wS-JQn3T-zEw")
    
    title, video_id, transcript = get_latest_video_transcript(channel_id)
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
