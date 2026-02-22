"""
Audio/Video transcription using Faster-Whisper.
Supports mp4, mp3, wav, m4a files.
"""
import os
from typing import Optional
from pathlib import Path

# Try to import faster_whisper, but allow fallback if not installed
try:
    from faster_whisper import WhisperModel
    WHISPER_AVAILABLE = True
except ImportError:
    WhisperModel = None  # type: ignore
    WHISPER_AVAILABLE = False

from app.config import settings
from app.logger import get_logger

logger = get_logger(__name__)

# Global model instance (loaded once)
_whisper_model: Optional["WhisperModel"] = None  # type: ignore


def get_whisper_model() -> "WhisperModel":  # type: ignore
    """
    Get or create the global Whisper model instance.
    Uses the base model by default for balance of speed and accuracy.
    
    Returns:
        WhisperModel instance
    """
    global _whisper_model
    
    if not WHISPER_AVAILABLE:
        raise RuntimeError("faster_whisper is not installed. Run: pip install faster-whisper")
    
    if _whisper_model is None:
        logger.info("Loading Faster-Whisper model...")
        
        # Use base model - good balance of speed and accuracy
        # Options: tiny, base, small, medium, large-v2, large-v3
        model_size = getattr(settings, 'whisper_model_size', 'base')
        
        # Use CPU by default, can use CUDA if available
        compute_type = getattr(settings, 'whisper_compute_type', 'int8')
        device = getattr(settings, 'whisper_device', 'cpu')
        
        _whisper_model = WhisperModel(  # type: ignore[misc]
            model_size,
            device=device,
            compute_type=compute_type
        )
        
        logger.info(f"Whisper model loaded: {model_size} on {device}")
    
    return _whisper_model


def transcribe_file(file_path: str) -> str:
    """
    Transcribe an audio/video file to text using Faster-Whisper.
    
    Args:
        file_path: Path to the audio/video file (mp4, mp3, wav, m4a)
        
    Returns:
        Full transcript text as a single string
        
    Raises:
        FileNotFoundError: If file doesn't exist
        ValueError: If file type is not supported
    """
    # Validate file exists
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    
    # Validate file extension
    supported_extensions = {'.mp4', '.mp3', '.wav', '.m4a', '.mpeg', '.webm'}
    if path.suffix.lower() not in supported_extensions:
        raise ValueError(
            f"Unsupported file type: {path.suffix}. "
            f"Supported types: {', '.join(supported_extensions)}"
        )
    
    logger.info(f"Starting transcription for: {file_path}")
    
    try:
        model = get_whisper_model()
        
        # Transcribe the file
        segments, info = model.transcribe(
            file_path,
            beam_size=5,
            language=None,  # Auto-detect language
            vad_filter=True,  # Voice activity detection for better results
            vad_parameters=dict(
                min_silence_duration_ms=500,
            )
        )
        
        logger.info(
            f"Detected language: {info.language} "
            f"(probability: {info.language_probability:.2f})"
        )
        
        # Concatenate all segments into full transcript
        transcript_parts = []
        for segment in segments:
            transcript_parts.append(segment.text.strip())
        
        full_transcript = " ".join(transcript_parts)
        
        logger.info(
            f"Transcription complete. "
            f"Length: {len(full_transcript)} characters"
        )
        
        return full_transcript
        
    except Exception as e:
        logger.error(f"Transcription failed for {file_path}: {e}")
        raise


def transcribe_file_with_timestamps(file_path: str) -> list:
    """
    Transcribe an audio/video file with timestamps.
    
    Args:
        file_path: Path to the audio/video file
        
    Returns:
        List of segments with start, end times and text
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    
    supported_extensions = {'.mp4', '.mp3', '.wav', '.m4a', '.mpeg', '.webm'}
    if path.suffix.lower() not in supported_extensions:
        raise ValueError(f"Unsupported file type: {path.suffix}")
    
    logger.info(f"Starting timestamped transcription for: {file_path}")
    
    try:
        model = get_whisper_model()
        
        segments, info = model.transcribe(
            file_path,
            beam_size=5,
            language=None,
            vad_filter=True
        )
        
        result = []
        for segment in segments:
            result.append({
                "start": segment.start,
                "end": segment.end,
                "text": segment.text.strip()
            })
        
        logger.info(f"Transcription complete. Segments: {len(result)}")
        return result
        
    except Exception as e:
        logger.error(f"Timestamped transcription failed for {file_path}: {e}")
        raise


def get_audio_duration(file_path: str) -> float:
    """
    Get the duration of an audio/video file in seconds.
    
    Args:
        file_path: Path to the audio/video file
        
    Returns:
        Duration in seconds
    """
    try:
        model = get_whisper_model()
        _, info = model.transcribe(file_path, beam_size=1)
        return info.duration
    except Exception as e:
        logger.error(f"Failed to get duration for {file_path}: {e}")
        return 0.0


def is_transcriber_ready() -> bool:
    """
    Check if the transcriber is ready to process files.
    Checks if faster_whisper is available (model loads on first use).
    
    Returns:
        True if faster_whisper is importable and configured
    """
    return WHISPER_AVAILABLE
