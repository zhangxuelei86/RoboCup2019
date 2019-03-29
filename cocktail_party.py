#! /usr/bin/env python
# -*- encoding: UTF-8 -*-
#! /usr/bin/env python
# -*- encoding: UTF-8 -*-
import qi
import os
import re
import time
import rospy
import thread
import atexit
import actionlib
from threading import Thread
from std_srvs.srv import Empty
from actionlib_msgs.msg import GoalID
from gender_predict import baidu_gender
from speech_recog import speech_recognition_text
from move_base_msgs.msg import MoveBaseAction, MoveBaseGoal
from geometry_msgs.msg import Twist, PoseStamped, PoseWithCovarianceStamped


class speech_person_recog():

    def __init__(self, params):
        atexit.register(self.__del__)
        # pepper connection
        self.ip = params["ip"]
        self.port = params["port"]
        self.session = qi.Session()
        rospy.init_node("cocktail_party")

        try:
            self.session.connect("tcp://" + self.ip + ":" + str(self.port))
        except RuntimeError:
            print("[Kamerider E] : connection Error!!")
            sys.exit(1)

        # naoqi API
        self.Leds = self.session.service("ALLeds")
        self.Memory = self.session.service("ALMemory")
        self.Dialog = self.session.service("ALDialog")
        self.Motion = self.session.service("ALMotion")
        self.AudioDev = self.session.service("ALAudioDevice")
        self.AudioPla = self.session.service("ALAudioPlayer")
        self.PhotoCap = self.session.service("ALPhotoCapture")
        self.RobotPos = self.session.service("ALRobotPosture")
        self.TextToSpe = self.session.service("ALTextToSpeech")
        self.AudioRec = self.session.service("ALAudioRecorder")
        self.BasicAwa = self.session.service("ALBasicAwareness")
        self.TabletSer = self.session.service("ALTabletService")
        self.SoundDet = self.session.service("ALSoundDetection")
        self.AutonomousLife = self.session.service("ALAutonomousLife")
        # stop recording
        try:
            self.AudioRec.stopMicrophonesRecording()
        except BaseException:
            print("\033[0;32;40m\t[Kamerider W]ALFaceCharacteristics : You don't need stop record\033[0m")
        # 录音的函数
        self.thread_recording = Thread(target=self.record_audio, args=(None,))
        self.thread_recording.daemon = True
        self.audio_terminate = False
        # ROS 订阅器和发布器
        self.nav_as = actionlib.SimpleActionClient("/move_base", MoveBaseAction)
        self.cmd_vel_pub = rospy.Publisher('/cmd_vel', Twist, queue_size=1)
        self.goal_cancel_pub = rospy.Publisher('/move_base/cancel', GoalID, queue_size=1)
        self.nav_as.wait_for_server()
        # 清除costmap
        self.map_clear_srv = rospy.ServiceProxy('/move_base/clear_costmaps', Empty)
        self.map_clear_srv()
        # amcl定位
        rospy.Subscriber('/amcl_pose', PoseWithCovarianceStamped, self.amcl_callback)
        #    LED的group
        self.led_name = ["Face/Led/Green/Right/0Deg/Actuator/Value", "Face/Led/Green/Right/45Deg/Actuator/Value",
                         "Face/Led/Green/Right/90Deg/Actuator/Value", "Face/Led/Green/Right/135Deg/Actuator/Value",
                         "Face/Led/Green/Right/180Deg/Actuator/Value", "Face/Led/Green/Right/225Deg/Actuator/Value",
                         "Face/Led/Green/Right/270Deg/Actuator/Value", "Face/Led/Green/Right/315Deg/Actuator/Value",
                         "Face/Led/Green/Left/0Deg/Actuator/Value", "Face/Led/Green/Left/45Deg/Actuator/Value",
                         "Face/Led/Green/Left/90Deg/Actuator/Value", "Face/Led/Green/Left/135Deg/Actuator/Value",
                         "Face/Led/Green/Left/180Deg/Actuator/Value", "Face/Led/Green/Left/225Deg/Actuator/Value",
                         "Face/Led/Green/Left/270Deg/Actuator/Value", "Face/Led/Green/Left/315Deg/Actuator/Value"]
        self.Leds.createGroup("MyGroup", self.led_name)
        # 声明一些变量
        self.current_person_name = []
        self.current_drink_name = []
        self.gender = "none"
        self.positive_list = ["yes", "of course", "we do"]
        self.negative_list = ["no", "sorry", "we don't", "shame"]
        self.name_list = ["Jack", "Jerry", "Tom"]
        self.drink_list = ["apple juice", "wine", "beer"]
        self.angle = -.25
        self.if_missing = True
        self.head_fix = True
        self.if_need_record = False
        self.point_dataset = self.load_waypoint("waypoints_party.txt")
        # 关闭basic_awareness
        if self.BasicAwa.isEnabled():
            self.BasicAwa.setEnabled(False)
        if self.BasicAwa.isRunning():
            self.BasicAwa.pauseAwareness()
        # 关闭AutonomousLife模式
        if self.AutonomousLife.getState() != "disabled":
            self.AutonomousLife.setState("disabled")
        # 初始化平板
        self.TabletSer.hideImage()
        print ('\033[0;32m [Kamerider I] Tablet initialize successfully \033[0m')
        # 设置dialog语言
        self.Dialog.setLanguage("English")
        # 录下的音频保存的路径
        self.audio_path = '/home/nao/audio/record.wav'
        self.recog_result = "None"
        self.RobotPos.goToPosture("StandInit", .2)
        print "getPostureList", self.RobotPos.getPostureList()
        print "getPosture", self.RobotPos.getPosture()
        print "getPostureFamily", self.RobotPos.getPostureFamily()
        print "getPostureFamilyList", self.RobotPos.getPostureFamilyList()
        # Beep 音量
        self.beep_volume = 70
        # 设置初始化头的位置 走路的时候固定头的位置
        #self.Motion.setStiffnesses("Head", 1.0)
        #self.Motion.setAngles("Head", [0., -0.25], .05)
        # 设置说话速度
        self.TextToSpe.setParameter("speed", 75.0)
        # 初始化录音
        self.record_delay = 2
        self.speech_hints = []
        self.enable_speech_recog = True
        # 1代表最灵敏
        self.SoundDet.setParameter("Sensitivity", .7)
        self.SoundDet.subscribe('sd')
        self.SoundDet_s = self.Memory.subscriber("SoundDetected")
        self.SoundDet_s.signal.connect(self.callback_sound_det)
        # 调用成员函数
        self.start_head_fix()
        self.set_volume(.7)
        self.keyboard_control()

    def __del__(self):
        print ('\033[0;32m [Kamerider I] System Shutting Down... \033[0m')
        self.AudioRec.stopMicrophonesRecording()

    def start_head_fix(self):
        arg = tuple([1])
        self.state = True
        thread.start_new_thread(self.head_fix_thread, arg)

    def show_person_image(self):
        cmd = "sshpass -p kurakura326 scp ./person_image/result.jpg nao@" + str(self.ip) + ":~/.local/share/PackageManager/apps/boot-config/html"
        os.system(cmd)
        self.TabletSer.hideImage()
        time.sleep(0.5)
        self.TabletSer.showImageNoCache("http://198.18.0.1/apps/boot-config/result.jpg")

    def head_fix_thread(self, arg):
        self.Motion.setStiffnesses("head", 1.0)
        while True:
            if self.head_fix:
                print "=====self.angle:====", self.angle
                self.Motion.setAngles("Head", [0., self.angle], .2)
            time.sleep(3)

    def say(self, text):
        self.TextToSpe.say(text)

    def amcl_callback(self, msg):
        self.car_pose= msg

    def load_waypoint(self, file_name):
        curr_pos = PoseStamped()
        f = open(file_name, 'r')
        sourceInLines = f.readlines()
        dataset_points = {}
        for line in sourceInLines:
            temp1 = line.strip('\n')
            temp2 = temp1.split(',')
            point_temp = MoveBaseGoal()
            point_temp.target_pose.header.frame_id = '/map'
            point_temp.target_pose.header.stamp = curr_pos.header.stamp
            point_temp.target_pose.header.seq = curr_pos.header.seq
            point_temp.target_pose.pose.position.x = float(temp2[1])
            point_temp.target_pose.pose.position.y = float(temp2[2])
            point_temp.target_pose.pose.position.z = float(temp2[3])
            point_temp.target_pose.pose.orientation.x = float(temp2[4])
            point_temp.target_pose.pose.orientation.y = float(temp2[5])
            point_temp.target_pose.pose.orientation.z = float(temp2[6])
            point_temp.target_pose.pose.orientation.w = float(temp2[7])
            dataset_points[temp2[0]] = point_temp
        print ("↓↓↓↓↓↓↓↓↓↓↓↓point↓↓↓↓↓↓↓↓↓↓↓↓")
        print (dataset_points)
        print ("↑↑↑↑↑↑↑↑↑↑↑↑point↑↑↑↑↑↑↑↑↑↑↑↑")
        print ('\033[0;32m [Kamerider I] Points Loaded! \033[0m')
        return dataset_points

    def set_volume(self, volume):
        self.TextToSpe.setVolume(volume)

    def callback_sound_det(self, msg):
        if self.if_need_record:
            print ('\033[0;32m [Kamerider I] Sound detected (In callback function) \033[0m')
            ox = 0
            for i in range(len(msg)):
                if msg[i][1] == 1:
                    ox = 1
            if ox == 1 and self.enable_speech_recog:
                self.record_time = time.time() + self.record_delay
                print "self.record_time += 2s ... ", self.record_time
                #if not self.thread_recording.is_alive():
                #    self.start_recording(reset=True)
                while self.recog_result == "None":
                    time.sleep(1)
                    continue
            else:
                return None
        else:
            print ('\033[0;32m [Kamerider I] Sound detected, but we don\'t need to record this audio \033[0m')

    def analyze_content(self):
        print "analyze_content"
        for i in range(len(self.name_list)):
            if re.search(self.name_list[i].lower(), self.recog_result) != None:
                self.recog_result = "None"
                # 记下当前人的名字
                self.current_person_name = self.name_list[i]
                self.TextToSpe.say("hey " + self.name_list[i] + " , what do you want")
                return
        for i in range(len(self.drink_list)):
            if re.search(self.drink_list[i].lower(), self.recog_result) != None:
                self.current_drink_name.append(self.drink_list[i])
            self.recog_result = "None"
            # 记下当前饮品的名字
            if self.current_drink_name != []:
                self.if_need_record = False
                self.TextToSpe.say("OK, I will take the " + self.current_drink_name + " to you")
                return

        for i in range(len(self.positive_list)):
            if re.search(self.positive_list[i].lower(), self.recog_result) != None:
                self.recog_result = "None"
                self.if_need_record = False
                self.if_missing = False
                self.TextToSpe.say("Ok, I will serve the other guest")
                return
        for i in range(len(self.negative_list)):
            if re.search(self.negative_list[i].lower(), self.recog_result) != None:
                self.recog_result = "None"
                self.if_need_record = False
                self.if_missing = True
                return
        self.say("sorry, please tell me again")
        self.start_recording(reset=True)
        self.analyze_content()

    def start_recording(self, reset=False, base_duration=3, withBeep=True):
        self.if_need_record = True
        self.record_time = time.time() + base_duration
        if reset:
            self.kill_recording_thread()
            self.AudioRec.stopMicrophonesRecording()
        print "self.record_time is:  ", self.record_time
        if not self.thread_recording.is_alive():
            self.thread_recording = Thread(target=self.record_audio, args=(self.speech_hints, withBeep))
            self.thread_recording.daemon = False
            self.thread_recording.start()
            self.thread_recording.join()
            print('\033[0;32m [Kamerider I] Start recording thread \033[0m')

    def record_audio(self, hints, withBeep = True):
        # 亮灯
        self.Leds.on("MyGroup")
        if withBeep:
            # 参数 playSine(const int& frequence, const int& gain, const int& pan, const float& duration)
            self.AudioPla.playSine(1000, self.beep_volume, 1, .3)
        print('\033[0;32m [Kamerider I] start recording... \033[0m')
        channels = [0, 0, 1, 0]
        self.AudioRec.startMicrophonesRecording("/home/nao/audio/recog.wav", "wav", 16000, channels)
        while time.time() < self.record_time:
            if self.audio_terminate:
                # 如果终止为True
                self.AudioRec.stopMicrophonesRecording()
                print('\033[0;32m [Kamerider I] Killing recording... \033[0m')
                return None
            time.sleep(.1)
        self.Leds.off("MyGroup")
        self.AudioRec.stopMicrophonesRecording()
        self.AudioRec.recording_ended = True
        if not os.path.exists('./audio_record'):
            # TODO
            os.mkdir('./audio_record', 0o755)
        # 复制录下的音频到自己的电脑上
        cmd = 'sshpass -p kurakura326 scp nao@' + str(self.ip) + ":/home/nao/audio/recog.wav ./audio_record"
        os.system(cmd)
        print('\033[0;32m [Kamerider I] Record ended start recognizing \033[0m')
        self.recog_result = speech_recognition_text.main("./audio_record/recog.wav").lower()
        print "===============", self.recog_result

    def take_picture(self):
        self.PhotoCap.takePictures(3, '/home/nao/picture', 'party')
        cmd = 'sshpass -p kurakura326 scp nao@' + str(self.ip) + ":/home/nao/picture/party_0.jpg ./person_image"
        os.system(cmd)
        _,_,self.gender = baidu_gender.gender_pre("./person_image/party_0.jpg")
        if self.gender == "none":
            self.say("there is no people in front of me")
            self.take_picture()

    def start(self):
        self.go_to_waypoint(self.point_dataset["party_room"], "party room", "first")
        # find people
        # TODO
        # start dialog
        self.dialog_with_people()

    def dialog_with_people(self):
        # 抬头
        self.angle = -.5
        time.sleep(1)
        # 拍照识别性别
        self.take_picture()
        self.show_person_image()
        self.say("Hi, my name is pepper, what is your name?")
        self.start_recording(reset=True)
        self.analyze_content()
        self.if_need_record = False
        self.say("Please tell me what do you want to order?")
        self.start_recording(reset=True)
        self.analyze_content()
        # 回到吧台
        #self.go_to_waypoint(self.point_dataset["party_room"], "party room", "first")
        print "======go back to the bar table======="
        if self.gender == "male":
            self.say("hey, the guy named " + self.current_person_name + " wanted a cup of " + self.current_drink_name + "and his gender is " + self.gender)
        else:
            self.say("hey, the guy named " + self.current_person_name + " wanted a cup of " + self.current_drink_name + "and her gender is " + self.gender)
        self.say("I'm not sure if you have this type drink, could you tell me?")
        self.start_recording(reset=True)
        self.analyze_content()
        if self.if_missing == True:
            # ask the alternatives drinks
            self.say("can you provide me the list of aviable alternatives?")
            self.start_recording(reset=True)
            self.analyze_content()
        else:
            print "go back to the party room"
            # self.go_to_waypoint(self.point_dataset["party_room"], "party room", "first")

    def kill_recording_thread(self):
        if self.thread_recording.is_alive():
            self.audio_terminate = True
            self.if_need_record = False

    def go_to_waypoint(self, Point, destination, label="none"): # Point代表目标点 destination代表目标点的文本 label
        self.angle = .1
        self.nav_as.send_goal(Point)
        self.map_clear_srv()
        count_time = 0
        # 等于3的时候就是到达目的地了
        while self.nav_as.get_state() != 3:
            count_time += 1
            time.sleep(1)
            if count_time == 3:
                self.map_clear_srv()
                count_time = 0
        self.TextToSpe.say("I have arrived at " + destination)
        if label == "none":
            return

    def keyboard_control(self):
        print('\033[0;32m [Kamerider I] Start keyboard control \033[0m')
        command = ''
        while command != 'c':
            try:
                command = raw_input('next command : ')
                if command == 'st':
                    self.start()
                elif command == 'c':
                    break
                elif command == 'dwp':
                    self.dialog_with_people()
                elif command == 'tp':
                    self.take_picture()
                else:
                    print("Invalid Command!")
            except EOFError:
                print "Error!!"

if __name__ == "__main__":
    params = {
        'ip': "192.168.3.18",
        'port': 9559
    }
    speech_person_recog(params)