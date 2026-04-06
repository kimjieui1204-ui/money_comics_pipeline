import os
import sys
import json
import logging
import requests
import xml.etree.ElementTree as ET
from datetime import datetime

# 1. 임포트 방식 변경 (클래스를 직접 가져옴)
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled
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
        
        skip_keywords = ["미에로화이바", "쇼츠", "Shorts", "예고편"]
        if any(keyword in video_title for keyword in skip_keywords):
            logging.warning(f"광고/쇼츠로 의심되는 영상입니다. 분석을 건너뜜: {video_title}")
            return video_title, video_id, "SKIP"
        
        logging.info("Downloading transcript in Korean...")
        
        # 2. 메서드 호출 방식 변경 (직관적이고 에러 확률 낮음)
        transcript_list = YouTubeTranscriptApi.get_transcript(video_id, languages=['ko', 'ko-KR'])
        transcript_text = " ".join([t['text'] for t in transcript_list])
        return video_title, video_id, transcript_text
        
    except TranscriptsDisabled: # 상단에서 import 했으므로 직접 참조
        logging.error("해당 영상에 자막이 비활성화되어 있습니다.")
        return video_title, video_id, "NO_TRANSCRIPT"
    except Exception as e:
        logging.error(f"Failed to get transcript. Error: {e}")
        return None, None, None

# ... (analyze_transcript, create_google_doc, send_telegram_message 함수는 그대로 유지) ...

def main():
    raw_channel_id = os.environ.get("YOUTUBE_CHANNEL_ID")
    if not raw_channel_id or raw_channel_id.strip() == "":
        channel_id = "UCJo6G1u0e_-wS-JQn3T-zEw" 
    else:
        channel_id = raw_channel_id.strip()
    
    title, video_id, transcript = get_latest_video_transcript(channel_id)
    
    if transcript in ["SKIP", "NO_TRANSCRIPT"]:
        msg = f"ℹ️ 오늘 영상 '{title}'은 분석 대상이 아니어 스킵했습니다."
        send_telegram_message(msg)
        sys.exit(0)
        
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
