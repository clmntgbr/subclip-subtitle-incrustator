import os
import ffmpeg
import uuid

from kombu import Queue
from flask import Flask
from celery import Celery

from src.config import Config
from src.s3_client import S3Client
from src.rabbitmq_client import RabbitMQClient
from src.file_client import FileClient
from src.converter import ProtobufConverter
from src.Protobuf.Message_pb2 import Clip, ClipStatus, SubtitleIncrustatorMessage, Video

app = Flask(__name__)
app.config.from_object(Config)
s3_client = S3Client(Config)
rmq_client = RabbitMQClient()
file_client = FileClient()

celery = Celery("tasks", broker=app.config["RABBITMQ_URL"])

celery.conf.update(
    {
        "task_serializer": "json",
        "accept_content": ["json"],
        "broker_connection_retry_on_startup": True,
        "task_routes": {
            "tasks.process_message": {"queue": app.config["RMQ_QUEUE_WRITE"]}
        },
        "task_queues": [
            Queue(
                app.config["RMQ_QUEUE_READ"], routing_key=app.config["RMQ_QUEUE_READ"]
            )
        ],
    }
)


@celery.task(name="tasks.process_message", queue=app.config["RMQ_QUEUE_READ"])
def process_message(message):
    clip: Clip = ProtobufConverter.json_to_protobuf(message)

    try:
        id = clip.id
        type = os.path.splitext(clip.originalVideo.name)[1]

        keyAss = f"{clip.userId}/{clip.id}/{id}.ass"
        keyVideoProcessed = f"{clip.userId}/{clip.id}/{id}_processed{type}"

        tmpVideoFilePath = f"/tmp/{id}{type}"
        tmpProcessedVideoFilePath = f"/tmp/{id}_processed{type}"
        tmpAssFilePath = f"/tmp/{id}.ass"

        if not s3_client.download_file(keyAss, tmpAssFilePath):
            raise Exception

        if not s3_client.download_file(keyVideoProcessed, tmpVideoFilePath):
            raise Exception

        ffmpeg.input(tmpVideoFilePath).output(
            tmpProcessedVideoFilePath,
            vf=f"subtitles={tmpAssFilePath}",
            vcodec="libx264",
            crf=23,
            preset="fast",
        ).run()

        if not s3_client.upload_file(tmpProcessedVideoFilePath, keyVideoProcessed):
            raise Exception

        file_client.delete_file(tmpAssFilePath)
        file_client.delete_file(tmpProcessedVideoFilePath)
        file_client.delete_file(tmpVideoFilePath)

        clip.status = ClipStatus.Name(ClipStatus.SUBTITLE_INCRUSTATOR_COMPLETE)

        protobuf = SubtitleIncrustatorMessage()
        protobuf.clip.CopyFrom(clip)

        rmq_client.send_message(protobuf, "App\\Protobuf\\SubtitleIncrustatorMessage")
    except Exception:
        clip.status = ClipStatus.Name(ClipStatus.SUBTITLE_INCRUSTATOR_ERROR)

        protobuf = SubtitleIncrustatorMessage()
        protobuf.clip.CopyFrom(clip)

        if not rmq_client.send_message(
            protobuf, "App\\Protobuf\\SubtitleIncrustatorMessage"
        ):
            return False