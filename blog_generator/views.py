from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from django.conf import settings
import json
from pytube import YouTube
import os
import assemblyai as aai
import openai
from .models import BlogPost
import time
from urllib.parse import urlparse, parse_qs
import requests
import yt_dlp

# Create your views here.
@login_required
def index(request):
    return render(request, 'index.html')

@csrf_exempt
def generate_blog(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            yt_link = data['link']
        except (KeyError, json.JSONDecodeError):
            return JsonResponse({'error': 'Invalid data sent'}, status=400)

        try:
            # get yt title
            title = yt_title(yt_link)
            if not title:
                return JsonResponse({'error': "Could not fetch video title"}, status=500)

            print(f"Successfully fetched video title: {title}")

            # get transcript
            print("Starting transcription process...")
            transcription = get_transcription(yt_link)
            if not transcription:
                return JsonResponse({'error': "Failed to get transcript"}, status=500)
            
            print(f"Successfully got transcript of length: {len(transcription)}")

            # use OpenAI to generate the blog
            print("Generating blog content...")
            blog_content = generate_blog_from_transcription(transcription)
            if not blog_content:
                return JsonResponse({'error': "Failed to generate blog article"}, status=500)

            # save blog article to database
            new_blog_article = BlogPost.objects.create(
                user=request.user,
                youtube_title=title,
                youtube_link=yt_link,
                generated_content=blog_content,
            )
            new_blog_article.save()

            return JsonResponse({'content': blog_content})
        except Exception as e:
            print(f"Error in generate_blog: {str(e)}")
            return JsonResponse({'error': str(e)}, status=500)
    else:
        return JsonResponse({'error': 'Invalid request method'}, status=405)

def yt_title(link):
    try:
        # Extract video ID from URL
        if 'youtu.be' in link:
            video_id = link.split('/')[-1].split('?')[0]
        elif 'youtube.com' in link:
            parsed_url = urlparse(link)
            video_id = parse_qs(parsed_url.query).get('v', [None])[0]
        else:
            raise ValueError("Not a YouTube URL")

        if not video_id:
            raise ValueError("Could not extract video ID")

        # Use YouTube oEmbed API (doesn't require API key)
        oembed_url = f'https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json'
        response = requests.get(oembed_url)
        
        if response.status_code == 200:
            data = response.json()
            return data.get('title')
        else:
            raise Exception(f"Failed to fetch title: HTTP {response.status_code}")

    except Exception as e:
        print(f"Error in yt_title: {str(e)}")
        return None

def download_audio(link):
    try:
        ydl_opts = {
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'outtmpl': f'{settings.MEDIA_ROOT}/%(title)s.%(ext)s',
            'quiet': True,
            'ffmpeg_location': '/usr/bin/ffmpeg',  # Update this path
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(link, download=True)
            file_path = ydl.prepare_filename(info_dict).replace('.webm', '.mp3').replace('.m4a', '.mp3')
            print(f"Successfully downloaded audio to: {file_path}")
            return file_path

    except Exception as e:
        print(f"Error in download_audio: {str(e)}")
        return None

def get_transcription(link):
    audio_file = None
    try:
        # Download the audio
        audio_file = download_audio(link)
        if not audio_file or not os.path.exists(audio_file):
            raise Exception("Audio file not downloaded properly")

        print(f"Starting transcription for file: {audio_file}")

        # Upload the audio file to AssemblyAI
        headers = {
            "authorization": "38a41932fb044a34b941f9fad18de942",
            "content-type": "application/json"
        }

        with open(audio_file, 'rb') as f:
            upload_response = requests.post("https://api.assemblyai.com/v2/upload", headers=headers, data=f)
        
        if upload_response.status_code != 200:
            print(f"Upload failed with status code: {upload_response.status_code}")
            print(f"Response: {upload_response.text}")
            raise Exception(f"Upload failed: {upload_response.text}")

        audio_url = upload_response.json()['upload_url']
        print(f"Audio uploaded successfully: {audio_url}")

        # Request transcription
        transcript_request = {
            "audio_url": audio_url,
            "language_code": "en"
        }

        transcript_response = requests.post("https://api.assemblyai.com/v2/transcript", json=transcript_request, headers=headers)

        if transcript_response.status_code != 200:
            print(f"Transcription request failed with status code: {transcript_response.status_code}")
            print(f"Response: {transcript_response.text}")
            raise Exception(f"Transcription request failed: {transcript_response.text}")

        transcript_id = transcript_response.json()['id']
        print(f"Transcription started: {transcript_id}")

        # Poll for completion
        while True:
            polling_response = requests.get(f"https://api.assemblyai.com/v2/transcript/{transcript_id}", headers=headers)
            polling_data = polling_response.json()

            print(f"Polling response: {polling_data}")

            if polling_data['status'] == 'completed':
                print("Transcription completed successfully.")
                return polling_data['text']
            elif polling_data['status'] == 'error':
                raise Exception(f"Transcription failed: {polling_data['error']}")
            
            print(f"Transcription status: {polling_data['status']}")
            time.sleep(3)

    except Exception as e:
        print(f"Transcription error: {str(e)}")
        return None

    finally:
        # Clean up the audio file
        if audio_file and os.path.exists(audio_file):
            try:
                os.remove(audio_file)
                print(f"Cleaned up audio file: {audio_file}")
            except Exception as e:
                print(f"Error cleaning up audio file: {str(e)}")




def generate_blog_from_transcription(transcription):
    api_key = 'IGmM9m8WH7nL1gTvoP0M4gi4UlpSa2meKhtqyHVg'  # Replace with your actual Cohere API key
    url = 'https://api.cohere.com/v2/generate'  # Ensure the endpoint is correct
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json'
    }
    data = {
        'model': 'command-r-plus',  # Ensure the model is compatible with v2
        'prompt': f"Based on the following transcript from a YouTube video, write a comprehensive blog article:\n\n{transcription}\n\nArticle:",
        'max_tokens': 1000,
        'temperature': 0.7
    }

    try:
        response = requests.post(url, headers=headers, json=data)
        if response.status_code == 200:
            result = response.json()
            # Check for 'generations' key and extract the text
            if 'generations' in result and len(result['generations']) > 0:
                generated_content = result['generations'][0]['text'].strip()
                return generated_content
            else:
                print("Error: 'generations' key not found or empty in the response")
                print("Full response:", result)  # Log the full response for debugging
                return None
        else:
            print(f"Error: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        print(f"Cohere API error: {str(e)}")
        return None



def blog_list(request):
    blog_articles = BlogPost.objects.filter(user=request.user)
    return render(request, "all-blogs.html", {'blog_articles': blog_articles})

def blog_details(request, pk):
    blog_article_detail = BlogPost.objects.get(id=pk)
    if request.user == blog_article_detail.user:
        return render(request, 'blog-details.html', {'blog_article_detail': blog_article_detail})
    else:
        return redirect('/')

def user_login(request):
    if request.method == 'POST':
        username = request.POST['username']
        password = request.POST['password']

        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            return redirect('/')
        else:
            error_message = "Invalid username or password"
            return render(request, 'login.html', {'error_message': error_message})
        
    return render(request, 'login.html')

def user_signup(request):
    if request.method == 'POST':
        username = request.POST['username']
        email = request.POST['email']
        password = request.POST['password']
        repeatPassword = request.POST['repeatPassword']

        if password == repeatPassword:
            try:
                user = User.objects.create_user(username, email, password)
                user.save()
                login(request, user)
                return redirect('/')
            except:
                error_message = 'Error creating account'
                return render(request, 'signup.html', {'error_message':error_message})
        else:
            error_message = 'Password do not match'
            return render(request, 'signup.html', {'error_message':error_message})
        
    return render(request, 'signup.html')

def user_logout(request):
    logout(request)
    return redirect('/')
