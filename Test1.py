from flask import Flask, request, jsonify, render_template, send_file
import moviepy.editor as mp
import whisper
import os
import tempfile
from functools import lru_cache
import warnings
warnings.filterwarnings("ignore", message="expected np.ndarray")

app = Flask(__name__)

# إعدادات
CLIP_DURATION = 10  # مدة كل مقطع مهم بالثواني

# قاموس الكلمات المفتاحية لكل رياضة
SPORT_KEYWORDS = {
    'Handball': [
        "goal", "هدف", "قول", 
        "save", "تصدي", "صد", 
        "penalty", "ركلة جزاء", "بلنتي", 
        "fast break", "هجمة مرتدة", "مرتدة سريعة", 
        "turnover", "فقدان الكرة", "خسارة الكرة", 
        "block", "اعتراض", "بلوك"
    ],
    'Martial Arts': [
        "knockout", "ضربة قاضية", "كي أو", 
        "submission", "اخضاع", "تسليم", 
        "takedown", "طرح أرضًا", "مسكة", 
        "punch", "لكمة", "بوكس", 
        "kick", "ركلة", "شوت", 
        "roundhouse", "ركلة دائرية", "ركلة دوران"
    ],
    'Car Racing': [
        "overtake", "تجاوز", "عداه", 
        "crash", "حادث", "خبط", "اصطدام", 
        "fastest lap", "أسرع لفة", "أسرع دورة", 
        "pit stop", "توقف للصيانة", "بيت ستوب", 
        "final lap", "اللفة الأخيرة", "الدورة الأخيرة", 
        "victory", "فوز", "ربح", "نصر"
    ],
    'Basketball': [
        "3-pointer", "ثلاثية", "ثلاث نقاط", 
        "slam dunk", "تغميسة", "دانك", 
        "fast break", "هجمة مرتدة", "مرتدة سريعة", 
        "steal", "سرقة الكرة", "خطف الكرة", 
        "assist", "تمريرة حاسمة", "باص حاسم", 
        "foul", "خطأ", "فاول"
    ],
    'Football': [
        "goal", "هدف", "قول", 
        "penalty kick", "ركلة جزاء", "بلنتي", 
        "shot", "تسديدة", "شوت", 
        "dangerous attack", "هجمة خطيرة", "هجمة قوية", 
        "corner kick", "ركنية", "ضربة زاوية", 
        "yellow card", "بطاقة صفراء", "كرت أصفر", 
        "red card", "بطاقة حمراء", "كرت أحمر"
    ]
}


# تحميل نموذج Whisper مع الذاكرة المؤقتة
@lru_cache(maxsize=1)
def load_whisper_model():
    return whisper.load_model("small")

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload():
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
        
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No file selected"}), 400

    sport_type = request.form.get('sport_type', 'Football')
    keywords = [kw.lower() for kw in SPORT_KEYWORDS.get(sport_type, [])]

    try:
        sport_type = request.form.get('sport_type')
        selected_moment = request.form.get('selected_moment')  # القيمة المحددة أو None
        
        # تحديد الكلمات المفتاحية للبحث
        if selected_moment and selected_moment != 'all':
            keywords = [selected_moment.replace('_', ' ')]  # تحويل penalty_kick إلى penalty kick
        else:
            keywords = SPORT_KEYWORDS.get(sport_type, [])
        
        # 1. حفظ الفيديو المؤقت (أسرع من استخدام NamedTemporaryFile)
        video_path = "temp_upload.mp4"
        file.save(video_path)
        
        # 2. استخراج الصوت مباشرة
        clip = mp.VideoFileClip(video_path)
        audio_path = "temp_audio.wav"
        clip.audio.write_audiofile(audio_path, logger=None, ffmpeg_params=["-ac", "1"])  # أحادي القناة أسرع
        
        # 3. تحليل الصوت
        model = load_whisper_model()
        result = model.transcribe(audio_path, fp16=False, verbose=None)
        
        # 4. تحديد اللحظات المهمة (بدون ThreadPoolExecutor)
        important_moments = []
        for segment in result["segments"]:
            text = segment["text"].lower()
            if any(keyword in text for keyword in keywords):
                important_moments.append(segment["start"])
        
        # 5. قص الفيديو
        if not important_moments:
            return jsonify({"error": "No highlights found"}), 404
            
        important_moments = sorted(important_moments)
        
        # 6. إنشاء الفيديو النهائي بإعدادات سريعة
        output_path = "highlights_output.mp4"
        subclips = [
            clip.subclip(max(0, t), min(t + CLIP_DURATION, clip.duration))
            for t in important_moments
        ]
        
        final_clip = mp.concatenate_videoclips(subclips)
        final_clip.write_videofile(
            output_path,
            codec="libx264",
            preset="ultrafast",  # أهم إعداد لتسريع المعالجة
            threads=8,          # استخدام كل الأنوية المتاحة
            ffmpeg_params=["-movflags", "+faststart"]
        )
        
        # 7. تنظيف الموارد
        clip.close()
        final_clip.close()
        for subclip in subclips:
            subclip.close()
            
        # 8. إرسال النتيجة
        response = send_file(
            output_path,
            as_attachment=True,
            mimetype='video/mp4',
            download_name=f"{sport_type}_highlights.mp4"
        )
        
        # 9. تنظيف الملفات المؤقتة بعد الإرسال
        @response.call_on_close
        def cleanup():
            for f in [video_path, audio_path, output_path]:
                try:
                    os.remove(f)
                except:
                    pass
                    
        return response

    except Exception as e:
        # تنظيف أي ملفات متبقية في حالة خطأ
        for f in ["temp_upload.mp4", "temp_audio.wav", "highlights_output.mp4"]:
            try:
                os.remove(f)
            except:
                pass
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, threaded=True)