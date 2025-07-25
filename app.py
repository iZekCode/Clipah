from flask import Flask, render_template, request, jsonify, send_file
import os
import json
import threading
import time
from datetime import datetime
import zipfile
import shutil
import subprocess
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

import yt_dlp
import assemblyai as aai
# from google import genai
import google.generativeai as genai
from moviepy import VideoFileClip
from moviepy.video.fx.Crop import Crop
from moviepy.video.fx.FadeIn import FadeIn
from moviepy.video.fx.FadeOut import FadeOut
from werkzeug.utils import secure_filename
import uuid

app = Flask(__name__)

# Configure Flask from environment variables
app.config['DEBUG'] = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'
app.config['ENV'] = os.getenv('FLASK_ENV', 'production')

# Add file upload configuration
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB max file size

# Ensure upload folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Allowed file extensions
ALLOWED_EXTENSIONS = {'mp4', 'avi', 'mov', 'mkv', 'webm', 'm4v', 'flv', '3gp'}

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Global variable to track processing status
processing_status = {
    'status': 'idle',
    'message': '',
    'progress': 0,
    'clips': [],
    'error': None
}

def cleanup_previous_output():
    """Clean up previous output files and folders"""
    items_to_remove = [
        'output_clips',
        'output_clips_final', 
        'output_subtitles',
        'final_clips.zip',
        'main_audio.mp3',
        'main_transcript.vtt',
        'main_video.webm',
        'main_video.mp4', 
        'raw_transcript.vtt',
        'clipah_clips.zip',
        'main_video.mhtml',
        'main_video.mkv',
        'temp-audio.m4a',
    ]
    
    print("🧹 Cleaning up previous output...")
    
    for item in items_to_remove:
        if os.path.exists(item):
            try:
                if os.path.isdir(item):
                    shutil.rmtree(item)
                    print(f" ✅ Removed folder: {item}")
                else:
                    os.remove(item)
                    print(f" ✅ Removed file: {item}")
            except Exception as e:
                print(f" ❌ Error removing {item}: {e}")
        else:
            print(f" ℹ️ Item not found, skipping: {item}")
    
    # Recreate uploads folder
    os.makedirs('uploads', exist_ok=True)
    
    print("✅ Cleanup complete!")

def log_progress(step, message, step_num=None, total_steps=None):
    """Update processing status"""
    global processing_status
    
    if step_num and total_steps:
        progress = int((step_num / total_steps) * 100)
        processing_status['progress'] = progress
    
    processing_status['status'] = 'processing'
    processing_status['message'] = f"{step}: {message}"
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {step}: {message}")

def time_to_seconds(time_str):
    """Convert time string (HH:MM:SS.mmm) to seconds"""
    try:
        parts = time_str.split(':')
        if len(parts) == 3:
            hours = int(parts[0])
            minutes = int(parts[1])
            seconds = float(parts[2])
            return hours * 3600 + minutes * 60 + seconds
        return 0
    except:
        return 0

def process_video_complete(video_source, source_type='url', language="Indonesian", include_subtitles=True, 
                         include_watermark=True, watermark_text="@clipah.com", aspect_ratio="9:16"):
    """
    Complete video processing pipeline from source to final clips
    """
    global processing_status
    
    try:
        processing_status['status'] = 'processing'
        processing_status['error'] = None
        
        # Set language code
        language_code = "en_us" if language.lower() == "english" else "id"
        
        # Initialize API clients
        assemblyai_api_key = os.getenv('ASSEMBLYAI_API_KEY')
        google_gemini_api_key = os.getenv('GOOGLE_GEMINI_API_KEY')
        
        if not assemblyai_api_key:
            raise RuntimeError("ASSEMBLYAI_API_KEY not found in environment variables")
        if not google_gemini_api_key:
            raise RuntimeError("GOOGLE_GEMINI_API_KEY not found in environment variables")
        
        aai.settings.api_key = assemblyai_api_key
        genai.configure(api_key=google_gemini_api_key)
        
        # Step counter
        total_steps = 7  
        if include_subtitles:
            total_steps += 2 
        elif include_watermark:  
            total_steps += 1 
        
        current_step = 0
        
        # Step 1: Handle Video Source
        current_step += 1
        
        if source_type == 'url':
            # Download from YouTube (existing logic)
            log_progress("Downloading video", f"Downloading video from: {video_source}", current_step, total_steps)
            ydl_opts = {
                'cookiefile': './cookies.txt',
                'outtmpl': 'main_video.%(ext)s',
                'format': 'best[height<=1080][ext=mp4]/best[height<=1080]/best[ext=mp4]/best'
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_source])
        else:
            # Handle uploaded file
            log_progress("Processing uploaded video", f"Processing uploaded file: {os.path.basename(video_source)}", current_step, total_steps)
            
            # Copy uploaded file to main_video.mp4 and convert if necessary
            file_extension = os.path.splitext(video_source)[1].lower()
            
            if file_extension == '.mp4':
                # If it's already MP4, just copy it
                shutil.copy2(video_source, 'main_video.mp4')
            else:
                # Convert to MP4 using FFmpeg
                try:
                    log_progress("Converting video format", f"Converting {file_extension} to MP4", current_step, total_steps)
                    result = subprocess.run([
                        'ffmpeg', '-i', video_source, 
                        '-c:v', 'libx264', '-c:a', 'aac',
                        '-preset', 'fast', '-crf', '23',
                        '-y', 'main_video.mp4'
                    ], capture_output=True, text=True, check=True, timeout=600)
                    
                    if not os.path.exists('main_video.mp4'):
                        raise RuntimeError("Failed to convert video to MP4")
                        
                except subprocess.CalledProcessError as e:
                    raise RuntimeError(f"Video conversion failed: {e.stderr}")
                except subprocess.TimeoutExpired:
                    raise RuntimeError("Video conversion timed out (file too large or corrupt)")
                except FileNotFoundError:
                    raise RuntimeError("FFmpeg not found. Please install FFmpeg for video conversion.")
        
        # Verify main video file exists
        if not os.path.exists('main_video.mp4'):
            raise RuntimeError("Failed to create main_video.mp4")
        
        # Step 2: Convert to MP3
        current_step += 1
        log_progress("Converting audio", "Converting video to MP3 format", current_step, total_steps)
        
        try:
            result = subprocess.run([
                'ffmpeg', '-i', 'main_video.mp4', '-y', 'main_audio.mp3'
            ], capture_output=True, text=True, check=True)
            
            if not os.path.exists('main_audio.mp3'):
                raise RuntimeError("Failed to create main_audio.mp3 - ffmpeg conversion failed")
                
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"FFmpeg conversion failed: {e.stderr}")
        except FileNotFoundError:
            raise RuntimeError("FFmpeg not found. Please install FFmpeg and add it to your PATH")
        
        # Step 3: Transcribe Audio
        current_step += 1
        log_progress("Transcribing audio", f"Transcribing audio in {language} language", current_step, total_steps)
        audio_file = "main_audio.mp3"
        main_config = aai.TranscriptionConfig(speech_model=aai.SpeechModel.universal, language_code=language_code)
        transcript = aai.Transcriber(config=main_config).transcribe(audio_file)
        
        if transcript.status == "error":
            raise RuntimeError(f"Transcription failed: {transcript.error}")
        
        # Step 4: Generate Raw Subtitles
        current_step += 1
        log_progress("Generating subtitles", "Creating VTT subtitle file", current_step, total_steps)
        
        def second_to_timecode(x: float) -> str:
            hour, x = divmod(x, 3600)
            minute, x = divmod(x, 60)
            second, x = divmod(x, 1)
            millisecond = int(x * 1000.)
            return '%.2d:%.2d:%.2d.%.3d' % (hour, minute, second, millisecond)
        
        def generate_subtitles_by_sentence(transcript):
            output = ["WEBVTT\n"]
            for sentence in transcript.get_sentences():
                start_time = second_to_timecode(sentence.start / 1000)
                end_time = second_to_timecode(sentence.end / 1000)
                subtitle_text = sentence.text
                output.append("%s --> %s" % (start_time, end_time))
                output.append(subtitle_text)
                output.append("")
            return output
        
        vtt = generate_subtitles_by_sentence(transcript)
        with open("raw_transcript.vtt", 'w') as o:
            final = '\n'.join(vtt)
            o.write(final)
        
        # Step 5: Speaker Diarization
        current_step += 1
        log_progress("Speaker diarization", "Identifying different speakers in the audio", current_step, total_steps)
        
        def read_file(file_path):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    return f.read()
            except FileNotFoundError:
                print(f"Error: The file '{file_path}' was not found.")
                return None
            except Exception as e:
                print(f"An error occurred while reading '{file_path}': {e}")
                return None
        
        def diarize_audio(audio_file_path, transcript_file_path):
            try:
                # audio_file = client.files.upload(path=audio_file_path)
                audio_file = genai.upload_file(path=audio_file_path)
            except Exception as e:
                print(f"Error uploading audio file: {e}")
                return None
            
            transcript_content = read_file(transcript_file_path)
            if not transcript_content:
                return None
            
            prompt = f"""
            You are an AI audio analysis expert specializing in speaker diarization.

            Your task is to analyze the provided audio file and its corresponding VTT transcript to determine who is speaking for each line of dialogue.

            **Follow these rules:**
            1.  Analyze the entire audio to identify the distinct speakers.
            2.  Label them sequentially as "Speaker A," "Speaker B," and so on.
            3.  Modify the input VTT content by adding the correct speaker label before each line of text. For example: `Gue yakin, hidup itu seperti matahari.` should become `Speaker A: Gue yakin, hidup itu seperti matahari.`
            4.  Do not modify the timestamps or any other part of the VTT file. Your only job is to insert the speaker labels.
            5.  Your final output should be the full, modified VTT content as a single block of text.

            **Input:**
            1.  The audio file.
            2.  The original VTT transcript content.

            Here is the transcript:
            ---
            {transcript_content}
            ---

            **Example Output:**
            ```vtt
            WEBVTT

            00:00:00.008 --> 00:00:03.228
            Speaker A: Gue yakin, hidup itu seperti matahari.

            00:00:03.228 --> 00:00:07.008
            Speaker A: Ya, kadang terbit, kadang juga terbenam.

            00:00:07.008 --> 00:00:10.247
            Speaker B: Dan bagaimana kamu menyikapi hal itu?
            """
            
            # response = client.models.generate_content(
            #     model="gemini-2.5-pro",
            #     contents=[prompt, audio_file]
            # )

            model = genai.GenerativeModel(model_name="gemini-2.5-pro")
            response = model.generate_content([prompt, audio_file])
            
            output_filename = "main_transcript.vtt"
            try:
                with open(output_filename, "w", encoding="utf-8") as f:
                    f.write(response.text)
            except Exception as e:
                print(f"Error saving file: {e}")
        
        diarize_audio("main_audio.mp3", "raw_transcript.vtt")
        
        # Step 6: Analyze Transcript and Get Clips
        current_step += 1
        log_progress("Analyzing transcript", "Finding the best segments for viral clips", current_step, total_steps)
        
        def analyze_transcript(vtt_content):
            prompt = f"""
            You are a world-class short-form viral video producer and editor with a deep understanding of narrative structure and audience retention. Your primary goal is to analyze the following transcript and extract segments that feel like **complete, satisfying mini-stories** or thoughts, avoiding clips that feel cut off or incomplete. The segments will be turned into short-form videos (like TikToks, Reels, Shorts).

            First, analyze the overall tone and topic of the transcript (e.g., 'Comedy Interview', 'Tech Tutorial', 'Motivational Speech').

            Then, based on that context, find the best segments that would make great clips by following these rules strictly:

            1.  **Identify Complete Narrative Arcs (Most Important Rule):** Do not just find a topic; find a complete thought. This could be a single person's monologue or a compelling exchange between speakers. Each segment must have a clear beginning (setup or question), a middle (the core message), and an end (a conclusion, punchline, or call to action). A viewer should feel satisfied that the idea was fully expressed.

            2.  **Clip Duration as a Guideline:** Aim for each segment to be between 30 and 70 seconds. However, **the completeness of the narrative arc is more important than the exact duration.** A perfect, complete 25-second or 75-second thought is better than a "half-baked" 45-second clip.

            3.  **Prioritize Virality & Engagement:** A great clip usually contains one or more of the following elements. Within a complete arc, look for these elements based on the video's context:
                * **For Educational/Serious Content:**
                    * A powerful metaphor or analogy.
                    * A strong, controversial, or counter-intuitive opinion.
                    * The single most important piece of advice or the key takeaway.
                    * A surprising fact or statistic.
                * **For Entertainment/Conversational Content:**
                    * A clear punchline to a joke or a moment of shared laughter.
                    * A short, funny, or relatable personal story (anecdote).
                    * A moment of genuine surprise or a sudden change in topic.
                * **For Multi-Speaker Content (Interviews, Podcasts):**
                    * A compelling question followed by a profound or unexpected answer.
                    * A moment of clear disagreement, debate, or differing opinions.
                    * A moment where one speaker successfully changes another's mind.
                * **For any Content:**
                    * A question posed directly to the audience.
                    * A clear "before and after" transformation.
                    * An emotional story.

            4.  **Provide Timestamps:** Use the timestamps from the transcript to provide the exact start and end time for each proposed clip.

            5.  **Strict Timestamp Formatting:** The start_time and end_time values must strictly adhere to the HH:MM:SS.mmm format. Always include two digits for the hour, even if they are zero. For example, use 00:01:23.456 and never 01:23.456.

            6.  **Language Consistency:** The clip_title and summary values must be written in the same language as the source transcript. Do not translate them. If the transcript is in Indonesian, the title and summary must also be in Indonesian.

            7.  **Point of View (Conditional & Intelligent):** The summary's point of view depends on the number of unique speakers identified *within that specific clip*:
                * **If a clip contains only one speaker:** Write the summary in the **first-person** from that speaker's perspective (e.g., "I explain why..." or "Menurut saya...").
                * **If a clip contains multiple speakers:**
                    * First, try to identify the speakers' actual names from the context of the full transcript (e.g., if Speaker A says "My name is Fuji").
                    * Write the summary in the **third-person** using their identified names (e.g., "Fuji asks about X, and Budi responds...").
                    * **If you cannot confidently identify their names,** fall back to using the provided labels (e.g., "Speaker A asks about X, and Speaker B responds...").

            8.  **Final Quality Check:** Before finalizing your JSON output, review each suggested clip. Ask yourself: "If I were a user, would this clip feel abrupt or incomplete?" If the answer is yes, adjust the timestamps to include the necessary context.

            9.  **Output in JSON:** Format your entire response as a single, valid JSON array. Each object must contain these exact keys: "clip_title", "start_time", "end_time", "summary", and "full_text". The "full_text" should be the complete text of that specific segment.

            Here is the transcript:
            ---
            {vtt_content}
            ---
            """
            
            try:
                # response = client.models.generate_content(
                #     model="gemini-2.5-pro", contents=prompt
                # )

                model = genai.GenerativeModel(model_name="gemini-2.5-pro")
                response = model.generate_content(prompt)

                cleaned_response_text = response.text.strip()
                
                if "```json" in cleaned_response_text:
                    cleaned_response_text = cleaned_response_text.split("```json")[1].split("```")[0].strip()
                elif "```" in cleaned_response_text:
                    cleaned_response_text = cleaned_response_text.split("```")[1].strip()
                
                suggested_clips = json.loads(cleaned_response_text)
                return suggested_clips
            except Exception as e:
                print(f"An error occurred during the API call or JSON parsing: {e}")
                return None
        
        transcript_content = read_file("main_transcript.vtt")
        clips = analyze_transcript(transcript_content)
        
        if not clips:
            raise RuntimeError("Failed to generate clips")
        
        # Step 7: Create Video Clips
        current_step += 1
        log_progress("Creating video clips", f"Cutting {len(clips)} video segments", current_step, total_steps)
        
        def create_video_clips(video_path, clips_to_generate, output_folder="output_clips"):
            if not os.path.exists(video_path):
                raise RuntimeError(f"Source video not found at '{video_path}'")
            
            if not os.path.exists(output_folder):
                os.makedirs(output_folder)
            
            try:
                source_video = VideoFileClip(video_path)
            except Exception as e:
                raise RuntimeError(f"Error loading video file: {e}")
            
            for i, clip_info in enumerate(clips_to_generate):
                clip_title = clip_info.get("clip_title", f"clip_{i+1}")
                start_time_str = clip_info.get("start_time")
                end_time_str = clip_info.get("end_time")

                start = time_to_seconds(start_time_str)
                end = time_to_seconds(end_time_str)
                
                safe_filename = "".join(c for c in clip_title if c.isalnum() or c in (' ', '_')).rstrip()
                output_path = os.path.join(output_folder, f"{i+1}_{safe_filename}.mp4")
                
                try:
                    print(f"[DEBUG] Creating clip {i+1}: '{clip_title}' from {start}s to {end}s")
                    
                    # Validate time range
                    if start >= end or start < 0:
                        print(f"[ERROR] Invalid time range for clip {i+1}: start={start}, end={end}")
                        continue
                    
                    # Ensure end time doesn't exceed video duration
                    video_duration = source_video.duration
                    if end > video_duration:
                        print(f"[WARNING] End time {end}s exceeds video duration {video_duration}s, adjusting")
                        end = video_duration - 0.1  # Leave small buffer
                    
                    # original_clip = source_video.subclip(start, end)
                    original_clip = source_video.subclipped(start, end)
                    
                    w, h = original_clip.size
                    print(f"[DEBUG] Original clip size: {w}x{h}, duration: {original_clip.duration}")
                    
                    # Validate clip dimensions and duration
                    if w <= 0 or h <= 0:
                        print(f"[ERROR] Invalid dimensions for clip {i+1}: {w}x{h}")
                        original_clip.close()
                        continue
                    
                    if original_clip.duration <= 0:
                        print(f"[ERROR] Invalid duration for clip {i+1}: {original_clip.duration}")
                        original_clip.close()
                        continue
                    
                    # Apply aspect ratio cropping
                    if aspect_ratio == "9:16":
                        target_width = int(h * 9 / 16)
                        if target_width > w:
                            print(f"[WARNING] Target width ({target_width}) > source width ({w}), using original")
                            final_clip = original_clip
                        else:
                            x_center = w // 2
                            x1 = max(0, x_center - target_width // 2)
                            x2 = min(w, x_center + target_width // 2)
                            # final_clip = Crop(original_clip, x1=x1, x2=x2)
                            final_clip = original_clip.with_effects([Crop(x1=x1, x2=x2)])
                    elif aspect_ratio == "16:9":
                        target_height = int(w * 9 / 16)
                        if target_height > h:
                            print(f"[WARNING] Target height ({target_height}) > source height ({h}), using original")
                            final_clip = original_clip
                        else:
                            y_center = h // 2
                            y1 = max(0, y_center - target_height // 2)
                            y2 = min(h, y_center + target_height // 2)
                            # final_clip = Crop(original_clip, y1=y1, y2=y2)
                            final_clip = original_clip.with_effects([Crop(y1=y1, y2=y2)])
                    else:
                        final_clip = original_clip
                    
                    print(f"[DEBUG] Final clip size: {final_clip.size}, duration: {final_clip.duration}")
                    
                    # Apply fade effects 
                    try:
                        if final_clip.duration > 1.0:  
                            fade_duration = min(0.5, final_clip.duration / 4)  
                            print(f"[DEBUG] Adding fade effects with duration: {fade_duration}")
                            # final_clip = FadeIn(final_clip, duration=fade_duration)
                            # final_clip = FadeOut(final_clip, duration=fade_duration)
                            final_clip = final_clip.with_effects([
                                FadeIn(duration=fade_duration),
                                FadeOut(duration=fade_duration)
                            ])
                        else:
                            print(f"[DEBUG] Clip too short for fade effects, skipping fades")
                    except Exception as fade_error:
                        print(f"[WARNING] Fade effects failed: {fade_error}, proceeding without fades")
                    
                    print(f"[DEBUG] Writing video file: {output_path}")
                    
                    # first attempt
                    try:
                        final_clip.write_videofile(
                            output_path, 
                            codec="libx264", 
                            preset="medium", 
                            logger=None,
                            threads=2 
                        )
                        print(f"[SUCCESS] Clip {i+1} created successfully: {output_path}")
                    except Exception as write_error:
                        print(f"[ERROR] Failed to write video file: {write_error}")

                        # second attempt
                        try:
                            print(f"[DEBUG] Retrying with different codec settings...")
                            final_clip.write_videofile(
                                output_path, 
                                codec="libx264", 
                                preset="fast",
                                logger=None,
                                threads=2 
                            )
                            print(f"[SUCCESS] Clip {i+1} created successfully on retry: {output_path}")
                        except Exception as retry_error:
                            print(f"[ERROR] Retry also failed: {retry_error}")
                            raise retry_error
                    
                    final_clip.close()
                    original_clip.close()
                    
                except Exception as e:
                    print(f"[ERROR] Error creating clip '{clip_title}': {e}")
                    print(f"[ERROR] Exception type: {type(e).__name__}")
                    import traceback
                    print(f"[ERROR] Full traceback:\n{traceback.format_exc()}")
                    
                    # Clean up any open clips
                    try:
                        if 'original_clip' in locals():
                            original_clip.close()
                        if 'final_clip' in locals() and final_clip != original_clip:
                            final_clip.close()
                    except:
                        pass
                    continue
            
            source_video.close()
        
        create_video_clips('main_video.mp4', clips)
        
        # Handle subtitles
        if include_subtitles:
            # Step 8: Create Subtitles
            current_step += 1
            log_progress("Creating subtitles", "Generating word-level subtitles for each clip", current_step, total_steps)

            def time_to_ms(time_str):
                parts = time_str.split(':')
                if len(parts) == 3:
                    h, m, s_ms = parts
                    s, ms = s_ms.split('.')
                    return (int(h) * 3600 + int(m) * 60 + int(s)) * 1000 + int(ms[:3])
                else:
                    try:
                        t = datetime.strptime(time_str, "%H:%M:%S.%f")
                        return (t.hour * 3600 + t.minute * 60 + t.second) * 1000 + int(t.microsecond / 1000)
                    except ValueError:
                        return 0

            def milliseconds_to_timecode(ms: int) -> str:
                seconds = ms / 1000.0
                hour, seconds = divmod(seconds, 3600)
                minute, seconds = divmod(seconds, 60)
                second, millisecond = divmod(seconds, 1)
                millisecond = int(millisecond * 1000)
                return '%.2d:%.2d:%.2d.%.3d' % (int(hour), int(minute), int(second), millisecond)

            def generate_word_level_subtitles(transcript, clip_start_ms):
                output = ["WEBVTT\n"]

                if not hasattr(transcript, 'words'):
                    print("Error: Transcript object does not contain word timestamps.")
                    return output

                for word in transcript.words:
                    adjusted_start_ms = word.start - clip_start_ms
                    adjusted_end_ms = word.end - clip_start_ms
                    adjusted_start_ms = max(0, adjusted_start_ms)
                    adjusted_end_ms = max(0, adjusted_end_ms)

                    start_timecode = milliseconds_to_timecode(adjusted_start_ms)
                    end_timecode = milliseconds_to_timecode(adjusted_end_ms)
                    word_text = word.text

                    output.append("%s --> %s" % (start_timecode, end_timecode))
                    output.append(word_text)
                    output.append("")

                return output

            output_subtitle_folder = "output_subtitles"
            if not os.path.exists(output_subtitle_folder):
                os.makedirs(output_subtitle_folder)

            for i, clip_info in enumerate(clips):
                clip_title = clip_info.get("clip_title", f"clip_{i+1}")
                start = clip_info.get("start_time")
                end = clip_info.get("end_time")

                if not start or not end:
                    continue

                start_ms = time_to_ms(start)
                end_ms = time_to_ms(end)

                print(f"   Creating subtitles for clip {i+1}/{len(clips)}: {clip_title}")

                clip_config = aai.TranscriptionConfig(speech_model=aai.SpeechModel.universal,
                                                      language_code=language_code,
                                                      audio_start_from=start_ms,
                                                      audio_end_at=end_ms)

                try:
                    transcript = aai.Transcriber(config=clip_config).transcribe(audio_file)

                    if transcript.status == "error":
                        continue

                    clip_start_ms = time_to_ms(start)
                    vtt = generate_word_level_subtitles(transcript, clip_start_ms)

                    if vtt:
                        safe_filename = "".join(c for c in clip_title if c.isalnum() or c in (' ', '_')).rstrip()
                        output_path = os.path.join(output_subtitle_folder, f"{i+1}_{safe_filename}_word.vtt")

                        with open(output_path, 'w') as o:
                            final = '\n'.join(vtt)
                            o.write(final)

                except Exception as e:
                    print(f"   Error processing subtitles for clip {i+1}: {e}")

            print("✅ Subtitles created successfully!")

            # Step 9: Apply Subtitles and Watermark
            current_step += 1
            log_progress("Finalizing clips", "Adding subtitles and watermark to video clips", current_step, total_steps)

            # Convert VTT to ASS and apply styling
            custom_style = """[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Montserrat,16,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,2,2,2,50,50,40,1
"""

            subtitle_files = [f for f in os.listdir(output_subtitle_folder) if f.endswith('_word.vtt')]

            for subtitle_file in subtitle_files:
                base_filename = subtitle_file.replace('_word.vtt', '')
                input_vtt_path = os.path.join(output_subtitle_folder, subtitle_file)
                output_ass_path = os.path.join(output_subtitle_folder, f"{base_filename}.ass")

                try:
                    subprocess.run([
                        'ffmpeg', '-i', input_vtt_path, output_ass_path, '-y'
                    ], capture_output=True, text=True, check=True)
                except subprocess.CalledProcessError as e:
                    print(f"FFmpeg VTT to ASS conversion failed for {subtitle_file}: {e.stderr}")
                    continue
                except FileNotFoundError:
                    print("FFmpeg not found for subtitle conversion.")
                    continue

                if os.path.exists(output_ass_path):
                    try:
                        with open(output_ass_path, "r", encoding="utf-8") as f:
                            lines = f.readlines()

                        new_lines = []
                        inside_style_block = False
                        for line in lines:
                            if line.strip().startswith("[V4+ Styles]"):
                                new_lines.append(custom_style + "\n")
                                inside_style_block = True
                            elif line.strip().startswith("[Events]"):
                                new_lines.append(line)
                                inside_style_block = False
                            elif not inside_style_block:
                                new_lines.append(line)

                        with open(output_ass_path, "w", encoding="utf-8") as f:
                            f.writelines(new_lines)

                    except Exception as e:
                        print(f"   Error styling subtitles: {e}")

            # Apply subtitles and watermark to final videos
            output_folder_clips = "output_clips"
            output_folder_final = "output_clips_final"

            if not os.path.exists(output_folder_final):
                os.makedirs(output_folder_final)

            subtitle_files = [f for f in os.listdir(output_subtitle_folder) if f.endswith('.ass')]

            print(f"[DEBUG] Found {len(subtitle_files)} subtitle files: {subtitle_files}")
            print(f"[DEBUG] Looking for clips in: {output_folder_clips}")
            if os.path.exists(output_folder_clips):
                clip_files = [f for f in os.listdir(output_folder_clips) if f.endswith('.mp4')]
                print(f"[DEBUG] Found {len(clip_files)} clip files: {clip_files}")
            else:
                print(f"[DEBUG] Clips folder doesn't exist: {output_folder_clips}")

            for i, subtitle_file in enumerate(subtitle_files):
                base_filename = subtitle_file.replace('.ass', '')
                input_video_path = os.path.join(output_folder_clips, f"{base_filename}.mp4")
                input_subtitle_path = os.path.join(output_subtitle_folder, subtitle_file)
                output_video_path = os.path.join(output_folder_final, f"{base_filename}_final.mp4")

                print(f"[DEBUG] Processing subtitle file: {subtitle_file}")
                print(f"[DEBUG] Looking for video file: {input_video_path}")
                print(f"[DEBUG] Video file exists: {os.path.exists(input_video_path)}")

                if not os.path.exists(input_video_path):
                    print(f"[DEBUG] Skipping {subtitle_file} - no matching video file")
                    continue

                print(f"   Finalizing clip {i+1}/{len(subtitle_files)}: {base_filename}")

                try:
                    escaped_subtitle_path = input_subtitle_path.replace('\\', '/').replace('output_subtitles/', './output_subtitles/')
                    
                    font_path = "styles/arial.ttf"

                    if include_watermark:
                        subprocess.run([
                            'ffmpeg', '-threads', '2', '-i', input_video_path,
                            '-vf', f"ass='{escaped_subtitle_path}',drawtext=text='{watermark_text}':fontfile='{font_path}':fontcolor=white@0.5:fontsize=10:x=(w-text_w)/2:y=h-text_h-15",
                            '-c:a', 'copy',
                            '-y',
                            output_video_path
                        ], capture_output=True, text=True, check=True, timeout=600)
                    else:
                        subprocess.run([
                            'ffmpeg', '-threads', '2', '-i', input_video_path,
                            '-vf', f"ass='{escaped_subtitle_path}'",
                            '-c:a', 'copy', 
                            '-y',
                            output_video_path
                        ], capture_output=True, text=True, check=True, timeout=600)
                        
                    if not os.path.exists(output_video_path):
                        raise RuntimeError(f"Failed to create final video: {output_video_path}")
                        
                except subprocess.CalledProcessError as e:
                    print(f"FFmpeg finalization failed for {base_filename}: {e.stderr}")
                    shutil.copy2(input_video_path, output_video_path)
                except FileNotFoundError:
                    print("FFmpeg not found for finalization. Copying original files.")
                    shutil.copy2(input_video_path, output_video_path)
                except Exception as e:
                    print(f"Unexpected error during finalization {base_filename}: {e}")
                    shutil.copy2(input_video_path, output_video_path)

        else:
            # If no subtitles, handle watermark as before
            if include_watermark:

                font_path = "styles/arial.ttf"

                current_step += 1
                log_progress("Adding watermark", "Adding watermark to video clips", current_step, total_steps)
                
                output_folder_clips = "output_clips"
                output_folder_final = "output_clips_final"
                if not os.path.exists(output_folder_final):
                    os.makedirs(output_folder_final)
                
                clip_files = [f for f in os.listdir(output_folder_clips) if f.endswith('.mp4')]
                for clip_file in clip_files:
                    input_video_path = os.path.join(output_folder_clips, clip_file)
                    output_video_path = os.path.join(output_folder_final, clip_file.replace('.mp4', '_final.mp4'))
                    
                    try:
                        result = subprocess.run([
                            'ffmpeg', '-i', input_video_path,
                            '-vf', f"drawtext=text='{watermark_text}':fontfile='{font_path}':fontcolor=white@0.5:fontsize=10:x=(w-text_w)/2:y=h-text_h-15",
                            '-c:a', 'copy',
                            '-y',
                            output_video_path
                        ], capture_output=True, text=True, check=True, timeout=600)
                        
                        if not os.path.exists(output_video_path):
                            raise RuntimeError(f"Failed to create watermarked video: {output_video_path}")
                            
                    except subprocess.CalledProcessError as e:
                        print(f"FFmpeg watermark failed for {clip_file}: {e.stderr}")
                        shutil.copy2(input_video_path, output_video_path)
                    except FileNotFoundError:
                        print("FFmpeg not found for watermarking. Copying original files.")
                        shutil.copy2(input_video_path, output_video_path)
                    except Exception as e:
                        print(f"Unexpected error during watermarking {clip_file}: {e}")
                        shutil.copy2(input_video_path, output_video_path)
            else:
                # Copy clips to final folder
                output_folder_clips = "output_clips"
                output_folder_final = "output_clips_final"
                if not os.path.exists(output_folder_final):
                    os.makedirs(output_folder_final)
                
                clip_files = [f for f in os.listdir(output_folder_clips) if f.endswith('.mp4')]
                for clip_file in clip_files:
                    input_video_path = os.path.join(output_folder_clips, clip_file)
                    output_video_path = os.path.join(output_folder_final, clip_file.replace('.mp4', '_final.mp4'))
                    shutil.copy2(input_video_path, output_video_path)

        # Create clip data summary file
        print("📝 Creating clip data summary...")

        clip_data_summary = []
        clip_data_summary.append("=" * 80)
        clip_data_summary.append("CLIPAH VIDEO PROCESSING SUMMARY")
        clip_data_summary.append("=" * 80)
        clip_data_summary.append(f"Video Source: {video_source}")
        clip_data_summary.append(f"Processing Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        clip_data_summary.append(f"Language: {language} ({language_code})")
        clip_data_summary.append(f"Aspect Ratio: {aspect_ratio}")
        clip_data_summary.append(f"Number of Clips Generated: {len(clips)}")
        clip_data_summary.append(f"Subtitles Included: {'Yes' if include_subtitles else 'No'}")
        clip_data_summary.append(f"Watermark Included: {'Yes' if include_watermark else 'No'}")
        if include_watermark:
            clip_data_summary.append(f"Watermark Text: {watermark_text}")
        clip_data_summary.append("")
        clip_data_summary.append("=" * 80)
        clip_data_summary.append("CLIP DETAILS")
        clip_data_summary.append("=" * 80)

        for i, clip in enumerate(clips, 1):
            clip_data_summary.append(f"\nCLIP {i}: {clip.get('clip_title', 'No Title')}")
            clip_data_summary.append("-" * (len(clip.get('clip_title', 'No Title')) + 10))
            clip_data_summary.append(f"Start Time: {clip.get('start_time', 'N/A')}")
            clip_data_summary.append(f"End Time: {clip.get('end_time', 'N/A')}")
            clip_data_summary.append(f"Summary: {clip.get('summary', 'N/A')}")
            clip_data_summary.append("")
            clip_data_summary.append("Full Text:")
            clip_data_summary.append(clip.get('full_text', 'N/A'))
            clip_data_summary.append("")
            clip_data_summary.append("-" * 80)

        # Save clip data to text file
        output_folder_final = "output_clips_final"
        clip_data_file = os.path.join(output_folder_final, "clip_data_summary.txt");

        try:
            with open(clip_data_file, 'w', encoding='utf-8') as f:
                f.write('\n'.join(clip_data_summary))
            print(f"✅ Clip data summary saved to: {clip_data_file}")
        except Exception as e:
            print(f"⚠️ Error saving clip data summary: {e}")
        
        # Update processing status with clips data
        processing_status['clips'] = clips
        processing_status['status'] = 'completed'
        processing_status['message'] = 'Processing completed successfully!'
        processing_status['progress'] = 100
        
        # Clean up uploaded file after processing
        if source_type == 'file' and os.path.exists(video_source):
            try:
                os.remove(video_source)
                print(f"✅ Cleaned up uploaded file: {video_source}")
            except Exception as e:
                print(f"⚠️ Could not clean up uploaded file: {e}")
        
        return True
        
    except Exception as e:
        processing_status['status'] = 'error'
        processing_status['error'] = str(e)
        processing_status['message'] = f'Error: {str(e)}'
        print(f"ERROR: {str(e)}")
        
        # Clean up uploaded file on error
        if 'video_source' in locals() and source_type == 'file' and os.path.exists(video_source):
            try:
                os.remove(video_source)
            except:
                pass
        
        return False

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/process', methods=['POST'])
def process_video():
    global processing_status
    
    try:
        # Handle both JSON and form data
        if request.is_json:
            # YouTube URL processing (existing)
            data = request.get_json()
            video_source = data.get('video_url')
            source_type = 'url'
        else:
            # File upload processing (new)
            data = request.form.to_dict()
            
            # Check if file was uploaded
            if 'video_file' not in request.files:
                return jsonify({'error': 'No video file uploaded'}), 400
            
            file = request.files['video_file']
            if file.filename == '' or not file:
                return jsonify({'error': 'No video file selected'}), 400
            
            if not allowed_file(file.filename):
                return jsonify({'error': 'File type not supported. Please upload MP4, AVI, MOV, MKV, WEBM, M4V, FLV, or 3GP files.'}), 400
            
            # Save uploaded file
            filename = secure_filename(file.filename)
            unique_filename = f"{uuid.uuid4().hex}_{filename}"
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
            file.save(file_path)
            
            video_source = file_path
            source_type = 'file'
        
        # Extract other parameters
        language = data.get('language', 'Indonesian')
        include_subtitles = data.get('include_subtitles', False)
        include_watermark = data.get('include_watermark', False)
        watermark_text = data.get('watermark_text', '@clipah.com')
        aspect_ratio = data.get('aspect_ratio', '9:16')
        
        if not video_source:
            return jsonify({'error': 'Video source is required (URL or file)'}), 400
        
        # Clean up previous files before starting
        cleanup_previous_output()
        
        # Reset processing status
        processing_status = {
            'status': 'starting',
            'message': 'Starting video processing...',
            'progress': 0,
            'clips': [],
            'error': None
        }
        
        # Start processing in background thread
        def process_in_background():
            process_video_complete(
                video_source=video_source,
                source_type=source_type,
                language=language,
                include_subtitles=include_subtitles,
                include_watermark=include_watermark,
                watermark_text=watermark_text,
                aspect_ratio=aspect_ratio
            )
        
        thread = threading.Thread(target=process_in_background)
        thread.daemon = True
        thread.start()
        
        return jsonify({'message': 'Processing started', 'status': 'started'})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/status')
def get_status():
    print(f"Status request - Current status: {processing_status['status']}")
    if processing_status['status'] == 'completed':
        print(f"Clips data being returned: {len(processing_status.get('clips', []))} clips")
        for i, clip in enumerate(processing_status.get('clips', [])):
            print(f"  Clip {i+1}: {clip.get('clip_title', 'No title')}")
    return jsonify(processing_status)

@app.route('/download')
def download_clips():
    try:
        # Create zip file of all clips
        output_folder = "output_clips_final"
        zip_filename = "clipah_clips.zip"
        
        if not os.path.exists(output_folder):
            return jsonify({'error': 'No clips available for download'}), 404
        
        clip_files = [f for f in os.listdir(output_folder) if f.endswith(('.mp4', '.txt'))]
        if not clip_files:
            return jsonify({'error': 'No video clips found'}), 404
        
        # Create zip file
        with zipfile.ZipFile(zip_filename, 'w') as zipf:
            for clip_file in clip_files:
                file_path = os.path.join(output_folder, clip_file)
                zipf.write(file_path, clip_file)
        
        return send_file(zip_filename, as_attachment=True, download_name='clipah_clips.zip')
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/reset', methods=['POST'])
def reset_processing():
    global processing_status
    
    # First, clean up previous output using your specified cleanup code
    cleanup_previous_output()
    
    # Reset processing status
    processing_status = {
        'status': 'idle',
        'message': '',
        'progress': 0,
        'clips': [],
        'error': None
    }
    
    return jsonify({'message': 'Reset completed', 'status': 'idle'})

@app.route('/output_clips/<filename>')
def serve_output_clip(filename):
    """Serve video files from output_clips folder"""
    try:
        return send_file(os.path.join('output_clips', filename), mimetype='video/mp4')
    except FileNotFoundError:
        return jsonify({'error': 'File not found'}), 404

@app.route('/output_clips_final/<filename>')
def serve_final_clip(filename):
    """Serve video files from output_clips_final folder"""
    try:
        return send_file(os.path.join('output_clips_final', filename), mimetype='video/mp4')
    except FileNotFoundError:
        return jsonify({'error': 'File not found'}), 404

if __name__ == '__main__':
    # Get configuration from environment variables with defaults
    debug = os.getenv('FLASK_DEBUG', 'True').lower() == 'true'
    host = os.getenv('FLASK_HOST', '0.0.0.0')
    port = int(os.getenv('FLASK_PORT', '5000'))
    
    app.run(debug=debug, host=host, port=port)
