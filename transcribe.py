import argparse
import base64
import configparser
import json
import threading
import time
import pyaudio
import websocket
from pygame import mixer
from websocket._abnf import ABNF
from ibm_watson import TextToSpeechV1, AssistantV2
from ibm_cloud_sdk_core.authenticators import IAMAuthenticator
CHUNK = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 1
# Rate is important, nothing works without it. This is a pretty
# standard default. If you have an audio device that requires
# something different, change this.
RATE = 44100
RECORD_SECONDS = 5
FINALS = []
LAST = None

REGION_MAP = {
    'us-east': 'gateway-wdc.watsonplatform.net',
    'us-south': 'stream.watsonplatform.net',
    'eu-gb': 'stream.watsonplatform.net',
    'eu-de': 'stream-fra.watsonplatform.net',
    'au-syd': 'gateway-syd.watsonplatform.net',
    'jp-tok': 'gateway-syd.watsonplatform.net',
}
def assistant():
    authenticator = IAMAuthenticator('umGGUqc2tw89lcBGy88PymGJXYftyUsHFQ_6iFR1ZMlh')
    
    assistant = AssistantV2(
    version='2020-04-01',
    authenticator = authenticator
    )

    assistant.set_service_url('https://api.us-south.assistant.watson.cloud.ibm.com/instances/b4b0ec73-bb5a-462e-bbfc-e0474c54c942')
     
    Session_response= assistant.create_session(
    assistant_id='096d0c86-8c79-4951-bbdb-b1387bc98a47'
    ).get_result()

    Session_id = Session_response['session_id']
  
    with open('outext.txt', 'r') as f:
        script = f.readlines()
  
    script = [line.replace('/n', '') for line in script]
    script = ''.join(str(line) for line in script)
    
    Assistant_response = assistant.message(
    assistant_id='096d0c86-8c79-4951-bbdb-b1387bc98a47',
    session_id = Session_id,
    input={
        'message_type': 'text',
        'text': script
    }
    ).get_result()
    data =  Assistant_response['output']['generic'][0]['text']
    with open('Res.txt', 'w') as f:
        f.write("\n"+data)
    
def ts():
    url = 'https://api.us-south.text-to-speech.watson.cloud.ibm.com/instances/6d0c018c-fa12-4540-84c6-448fb517d392'
    apikey = 'FUsEaqvbD0bPcLbYtN9jMn56Jb5ruLZ1KilB1p3HUjVV'
    authenticator = IAMAuthenticator(apikey)
    tts = TextToSpeechV1(authenticator=authenticator)
    tts.set_service_url(url)

    with open('Res.txt', 'r') as f:
        script = f.readlines()
    
    script = [line.replace('/n', '') for line in script]
    script = ''.join(str(line) for line in script)

    print('Response : '+ script)

    with open('speech.mp3', 'wb') as audio_file:
        result = tts.synthesize(script, accept='audio/mp3', voice='en-US_AllisonV3Voice').get_result()
        audio_file.write(result.content)
        
    mixer.init()
    a = mixer.Sound('speech.mp3')
    duration = a.get_length()
    a.play()
    time.sleep(duration)
    main()
    assistant()
    ts()
def read_audio(ws, timeout):
    """Read audio and sent it to the websocket port.
    This uses pyaudio to read from a device in chunks and send these
    over the websocket wire.
    """
    global RATE
    p = pyaudio.PyAudio()
    # NOTE(sdague): if you don't seem to be getting anything off of
    # this you might need to specify:
    #
    #    input_device_index=N,
    #
    # Where N is an int. You'll need to do a dump of your input
    # devices to figure out which one you want.
    RATE = int(p.get_default_input_device_info()['defaultSampleRate'])
    stream = p.open(format=FORMAT,
                    channels=CHANNELS,
                    rate=RATE,
                    input=True,
                    frames_per_buffer=CHUNK)

    print("* recording")
    rec = timeout or RECORD_SECONDS

    for i in range(0, int(RATE / CHUNK * rec)):
        data = stream.read(CHUNK)
        # print("Sending packet... %d" % i)
        # NOTE(sdague): we're sending raw binary in the stream, we
        # need to indicate that otherwise the stream service
        # interprets this as text control messages.
        ws.send(data, ABNF.OPCODE_BINARY)

    # Disconnect the audio stream
    stream.stop_stream()
    stream.close()
    print("* done recording")

    # In order to get a final response from STT we send a stop, this
    # will force a final=True return message.
    data = {"action": "stop"}
    ws.send(json.dumps(data).encode('utf8'))
    # ... which we need to wait for before we shutdown the websocket
    time.sleep(1)
    ws.close()

    # ... and kill the audio device
    p.terminate()


def on_message(self, msg):
    """Print whatever messages come in.
    While we are processing any non trivial stream of speech Watson
    will start chunking results into bits of transcripts that it
    considers "final", and start on a new stretch. It's not always
    clear why it does this. However, it means that as we are
    processing text, any time we see a final chunk, we need to save it
    off for later.
    """
    global LAST
    data = json.loads(msg)
    if "results" in data:
        if data["results"][0]["final"]:
            FINALS.append(data)
            LAST = None
        else:
            LAST = data
        # This prints out the current fragment that we are working on
        print(data['results'][0]['alternatives'][0]['transcript'])
        with open('outext.txt', 'w') as output:
           output.write(data['results'][0]['alternatives'][0]['transcript'])
        


def on_error(self, error):
    """Print any errors."""
    print(error)


def on_close(ws):
    """Upon close, print the complete and final transcript."""
    global LAST
    if LAST:
        FINALS.append(LAST)
    transcript = "".join([x['results'][0]['alternatives'][0]['transcript']
                          for x in FINALS])
    print(transcript)


def on_open(ws):
    """Triggered as soon a we have an active connection."""
    args = ws.args
    data = {
        "action": "start",
        # this means we get to send it straight raw sampling
        "content-type": "audio/l16;rate=%d" % RATE,
        "continuous": True,
        "interim_results": True,
        # "inactivity_timeout": 5, # in order to use this effectively
        # you need other tests to handle what happens if the socket is
        # closed by the server.
        "word_confidence": True,
        "timestamps": True,
        "max_alternatives": 3
    }

    # Send the initial control message which sets expectations for the
    # binary stream that follows:
    ws.send(json.dumps(data).encode('utf8'))
    # Spin off a dedicated thread where we are going to read and
    # stream out audio.
    threading.Thread(target=read_audio,
                     args=(ws, args.timeout)).start()

def get_url():
    config = configparser.RawConfigParser()
    config.read('speech.cfg')
    # See
    # https://console.bluemix.net/docs/services/speech-to-text/websockets.html#websockets
    # for details on which endpoints are for each region.
    region = config.get('auth', 'region')
    host = REGION_MAP[region]
    return ("wss://{}/speech-to-text/api/v1/recognize"
           "?model=en-AU_BroadbandModel").format(host)

def get_auth():
    config = configparser.RawConfigParser()
    config.read('speech.cfg')
    apikey = config.get('auth', 'apikey')
    return ("apikey", apikey)


def parse_args():
    parser = argparse.ArgumentParser(
        description='Transcribe Watson text in real time')
    parser.add_argument('-t', '--timeout', type=int, default=5)
    # parser.add_argument('-d', '--device')
    # parser.add_argument('-v', '--verbose', action='store_true')
    args = parser.parse_args()
    return args


def main():
    # Connect to websocket interfaces
    headers = {}
    userpass = ":".join(get_auth())
    headers["Authorization"] = "Basic " + base64.b64encode(
        userpass.encode()).decode()
    url = get_url()

    # If you really want to see everything going across the wire,
    # uncomment this. However realize the trace is going to also do
    # things like dump the binary sound packets in text in the
    # console.
    #
    # websocket.enableTrace(True)
    ws = websocket.WebSocketApp(url,
                                header=headers,
                                on_message=on_message,
                                on_error=on_error,
                                on_close=on_close)
    ws.on_open = on_open
    ws.args = parse_args()
    # This gives control over the WebSocketApp. This is a blocking
    # call, so it won't return until the ws.close() gets called (after
    # 6 seconds in the dedicated thread).
    ws.run_forever()
    
    
if __name__ == "__main__":
    main()
    assistant()
    ts()
     