#include <Arduino.h>
#include <Arduino_RouterBridge.h>

#include <U8g2lib.h>
#include <Wire.h>
#include <Servo.h>


// ============================================================
// L298N MOTOR PINS
// ============================================================

#define ENA  9
#define IN1  7
#define IN2  8

#define IN3  12
#define IN4  13
#define ENB  10


// ============================================================
// SERVO
// ============================================================

#define SERVO_PIN 6

Servo headServo;

int servoAngle = 90;

// Keep the head inside the mechanical limits of this robot.
const int SERVO_MIN = 55;
const int SERVO_MAX = 135;


// ============================================================
// MOTOR SETTINGS
// ============================================================

// The L298N can be driven harder from the manual UI, but 70 is the
// deliberately gentle speed used by this companion robot.
const int SAFE_MOTOR_SPEED = 70;
int motorSpeed = SAFE_MOTOR_SPEED;


// Timed motion is used by the voice controller. The deadline lives on the
// MCU, so a timed voice command still stops if Python disconnects.
bool timedMotionActive = false;
unsigned long timedMotionStopAt = 0;

bool LEFT_REVERSED  = false;
bool RIGHT_REVERSED = false;


// ============================================================
// OLED
// ============================================================

U8G2_SH1106_128X64_NONAME_F_HW_I2C u8g2(
  U8G2_R0,
  U8X8_PIN_NONE
);


// ============================================================
// EYE SETTINGS
// ============================================================

const int EYE_W = 42;
const int EYE_H = 42;

const int EYE_RADIUS = 10;

const int LEFT_BASE_X  = 11;
const int RIGHT_BASE_X = 75;

const int BASE_Y = 11;


// ============================================================
// EYE POSITION
// ============================================================

float eyeX = 0;
float eyeY = 0;

float targetX = 0;
float targetY = 0;


// ============================================================
// EYE OPENNESS
// ============================================================

float openness = 1.0;
float targetOpenness = 1.0;


// ============================================================
// MOODS
// ============================================================

enum EyeMood {

  NORMAL = 0,
  HAPPY = 1,
  ANGRY = 2,
  TIRED = 3,
  CURIOUS = 4

};

EyeMood currentMood = NORMAL;


// ============================================================
// AUTO EMOTION
// ============================================================

bool autoEmotion = true;

unsigned long nextEmotionTime = 8000;
unsigned long emotionEndTime = 0;

bool temporaryEmotion = false;


// ============================================================
// RANDOM LOOK
// ============================================================

unsigned long lastLook = 0;
unsigned long nextLook = 2000;


// ============================================================
// BLINK SETTINGS
// ============================================================

unsigned long lastBlink = 0;
unsigned long nextBlink = 3500;


// ============================================================
// NATURAL BLINK STATE
// ============================================================

enum BlinkState {

  BLINK_IDLE,
  BLINK_CLOSING,
  BLINK_CLOSED,
  BLINK_OPENING

};

BlinkState blinkState = BLINK_IDLE;

unsigned long blinkStartTime = 0;


// Natural blink timings

const unsigned long BLINK_CLOSE_TIME = 85;
const unsigned long BLINK_HOLD_TIME  = 30;
const unsigned long BLINK_OPEN_TIME  = 135;


// Double blink

bool waitingSecondBlink = false;

unsigned long secondBlinkAt = 0;


// ============================================================
// MOTOR - LEFT
// ============================================================

void setLeftMotor(int direction) {

  if (LEFT_REVERSED) {
    direction = -direction;
  }


  if (direction > 0) {

    digitalWrite(IN1, HIGH);
    digitalWrite(IN2, LOW);

    analogWrite(
      ENA,
      motorSpeed
    );
  }

  else if (direction < 0) {

    digitalWrite(IN1, LOW);
    digitalWrite(IN2, HIGH);

    analogWrite(
      ENA,
      motorSpeed
    );
  }

  else {

    digitalWrite(IN1, LOW);
    digitalWrite(IN2, LOW);

    analogWrite(
      ENA,
      0
    );
  }
}


// ============================================================
// MOTOR - RIGHT
// ============================================================

void setRightMotor(int direction) {

  if (RIGHT_REVERSED) {
    direction = -direction;
  }


  if (direction > 0) {

    digitalWrite(IN3, HIGH);
    digitalWrite(IN4, LOW);

    analogWrite(
      ENB,
      motorSpeed
    );
  }

  else if (direction < 0) {

    digitalWrite(IN3, LOW);
    digitalWrite(IN4, HIGH);

    analogWrite(
      ENB,
      motorSpeed
    );
  }

  else {

    digitalWrite(IN3, LOW);
    digitalWrite(IN4, LOW);

    analogWrite(
      ENB,
      0
    );
  }
}


// ============================================================
// WEB DRIVE
// ============================================================

bool drive(int left, int right) {

  // Legacy/manual drive intentionally remains latched until /stop. Voice
  // control uses moveTimed() below, which has an MCU-enforced stop.
  timedMotionActive = false;

  left = constrain(
    left,
    -1,
    1
  );

  right = constrain(
    right,
    -1,
    1
  );


  setLeftMotor(left);
  setRightMotor(right);


  return true;
}


// ============================================================
// STOP MOTORS
// ============================================================

bool stopMotors() {

  timedMotionActive = false;

  setLeftMotor(0);
  setRightMotor(0);

  return true;
}


// ============================================================
// MOTOR SPEED
// ============================================================

bool setSpeed(int speed) {

  motorSpeed =
    constrain(
      speed,
      0,
      SAFE_MOTOR_SPEED
    );


  Monitor.print(
    "Speed: "
  );

  Monitor.println(
    motorSpeed
  );


  return true;
}


// ============================================================
// TIMED MOTION
//
// Starts a move and lets loop() stop it after the requested time. This must
// be used for every non-continuous Gemini movement command.
// ============================================================

bool moveTimed(
  int left,
  int right,
  int durationMs
) {

  left = constrain(
    left,
    -1,
    1
  );

  right = constrain(
    right,
    -1,
    1
  );

  durationMs = constrain(
    durationMs,
    100,
    5000
  );

  setLeftMotor(left);
  setRightMotor(right);

  timedMotionStopAt =
    millis() +
    durationMs;

  timedMotionActive = true;

  Monitor.print(
    "Timed motion: "
  );

  Monitor.println(
    durationMs
  );

  return true;
}


// ============================================================
// SERVO
// ============================================================

bool setServo(int angle) {

  servoAngle =
    constrain(
      angle,
      SERVO_MIN,
      SERVO_MAX
    );


  headServo.write(
    servoAngle
  );


  Monitor.print(
    "Servo: "
  );

  Monitor.println(
    servoAngle
  );


  return true;
}


// ============================================================
// DRAW SINGLE EYE
// ============================================================

void drawEye(
  int x,
  int y,
  int width,
  int height,
  bool leftEye
) {

  // ==========================================================
  // CLOSED EYE
  // ==========================================================

  if (height <= 3) {

    int cy =
      y +
      EYE_H / 2;


    // Slightly curved / soft closed eyelid

    u8g2.drawHLine(
      x + 4,
      cy,
      width - 8
    );


    u8g2.drawPixel(
      x + 3,
      cy - 1
    );


    u8g2.drawPixel(
      x + width - 4,
      cy - 1
    );


    return;
  }


  // ==========================================================
  // NATURAL EYELID MOVEMENT
  //
  // 75% of the missing height is taken from the top,
  // instead of squeezing equally from top and bottom.
  // ==========================================================

  int missingHeight =
    EYE_H -
    height;


  int topOffset =
    (missingHeight * 3) /
    4;


  y +=
    topOffset;


  // ==========================================================
  // MAIN EYE
  // ==========================================================

  u8g2.setDrawColor(
    1
  );


  int radius =
    min(
      EYE_RADIUS,
      max(
        2,
        height / 4
      )
    );


  u8g2.drawRBox(
    x,
    y,
    width,
    height,
    radius
  );


  // ==========================================================
  // EXPRESSION CUTOUTS
  // ==========================================================

  u8g2.setDrawColor(
    0
  );


  // ----------------------------------------------------------
  // HAPPY
  // ----------------------------------------------------------

  if (currentMood == HAPPY) {

    u8g2.drawDisc(
      x + width / 2,
      y + height + 8,
      width / 2 + 5
    );
  }


  // ----------------------------------------------------------
  // ANGRY
  // ----------------------------------------------------------

  else if (currentMood == ANGRY) {

    if (leftEye) {

      u8g2.drawTriangle(

        x,
        y,

        x + width,
        y,

        x + width,
        y + 13

      );
    }

    else {

      u8g2.drawTriangle(

        x,
        y,

        x + width,
        y,

        x,
        y + 13

      );
    }
  }


  // ----------------------------------------------------------
  // TIRED
  // ----------------------------------------------------------

  else if (currentMood == TIRED) {

    int tiredCut =
      min(
        12,
        height / 3
      );


    u8g2.drawBox(
      x,
      y,
      width,
      tiredCut
    );
  }


  // ----------------------------------------------------------
  // CURIOUS
  // ----------------------------------------------------------

  else if (currentMood == CURIOUS) {

    if (leftEye) {

      int curiousCut =
        min(
          5,
          height / 5
        );


      u8g2.drawBox(
        x,
        y,
        width,
        curiousCut
      );
    }
  }


  u8g2.setDrawColor(
    1
  );
}


// ============================================================
// DRAW BOTH EYES
// ============================================================

void drawEyes() {

  u8g2.clearBuffer();


  int offsetX =
    (int)eyeX;


  int offsetY =
    (int)eyeY;


  int currentHeight =
    (int)(
      EYE_H *
      openness
    );


  currentHeight =
    constrain(
      currentHeight,
      0,
      EYE_H
    );


  int leftX =
    LEFT_BASE_X +
    offsetX;


  int rightX =
    RIGHT_BASE_X +
    offsetX;


  int y =
    BASE_Y +
    offsetY;


  drawEye(
    leftX,
    y,
    EYE_W,
    currentHeight,
    true
  );


  drawEye(
    rightX,
    y,
    EYE_W,
    currentHeight,
    false
  );


  u8g2.sendBuffer();
}


// ============================================================
// SMOOTH EYE POSITION
// ============================================================

void updateEyeMovement() {

  eyeX +=
    (
      targetX -
      eyeX
    ) *
    0.18;


  eyeY +=
    (
      targetY -
      eyeY
    ) *
    0.18;
}


// ============================================================
// SMOOTH STEP
// ============================================================

float smoothStep(float t) {

  t =
    constrain(
      t,
      0.0f,
      1.0f
    );


  return
    t *
    t *
    (
      3.0f -
      2.0f *
      t
    );
}


// ============================================================
// START BLINK
// ============================================================

void startBlink() {

  if (
    blinkState !=
    BLINK_IDLE
  ) {

    return;
  }


  blinkState =
    BLINK_CLOSING;


  blinkStartTime =
    millis();
}


// ============================================================
// NATURAL BLINK UPDATE
// ============================================================

void updateBlink() {

  unsigned long now =
    millis();


  unsigned long elapsed =
    now -
    blinkStartTime;


  // ==========================================================
  // IDLE
  // ==========================================================

  if (
    blinkState ==
    BLINK_IDLE
  ) {

    openness =
      1.0;

    return;
  }


  // ==========================================================
  // CLOSING
  // ==========================================================

  if (
    blinkState ==
    BLINK_CLOSING
  ) {

    float t =
      (float)elapsed /
      (float)BLINK_CLOSE_TIME;


    if (t >= 1.0) {

      openness =
        0.0;


      blinkState =
        BLINK_CLOSED;


      blinkStartTime =
        now;


      return;
    }


    float eased =
      smoothStep(t);


    openness =
      1.0 -
      eased;


    return;
  }


  // ==========================================================
  // CLOSED
  // ==========================================================

  if (
    blinkState ==
    BLINK_CLOSED
  ) {

    openness =
      0.0;


    if (
      elapsed >=
      BLINK_HOLD_TIME
    ) {

      blinkState =
        BLINK_OPENING;


      blinkStartTime =
        now;
    }


    return;
  }


  // ==========================================================
  // OPENING
  // ==========================================================

  if (
    blinkState ==
    BLINK_OPENING
  ) {

    float t =
      (float)elapsed /
      (float)BLINK_OPEN_TIME;


    if (t >= 1.0) {

      openness =
        1.0;


      blinkState =
        BLINK_IDLE;


      return;
    }


    float eased =
      smoothStep(t);


    openness =
      eased;


    return;
  }
}


// ============================================================
// LOOK FUNCTIONS
// ============================================================

void lookCenter() {

  targetX = 0;
  targetY = 0;
}


void lookLeft() {

  targetX = -8;
  targetY = 0;
}


void lookRight() {

  targetX = 8;
  targetY = 0;
}


void lookUp() {

  targetX = 0;
  targetY = -6;
}


void lookDown() {

  targetX = 0;
  targetY = 6;
}


// ============================================================
// RANDOM LOOK
// ============================================================

void randomLook() {

  int direction =
    random(
      0,
      9
    );


  switch (direction) {


    case 0:

      lookLeft();

      break;


    case 1:

      lookRight();

      break;


    case 2:

      lookUp();

      break;


    case 3:

      lookDown();

      break;


    case 4:

      targetX = -7;
      targetY = -5;

      break;


    case 5:

      targetX = 7;
      targetY = -5;

      break;


    case 6:

      targetX = -7;
      targetY = 5;

      break;


    case 7:

      targetX = 7;
      targetY = 5;

      break;


    default:

      lookCenter();

      break;
  }
}


// ============================================================
// SET EMOTION FROM WEB
//
// 0 = NORMAL
// 1 = HAPPY
// 2 = ANGRY
// 3 = TIRED
// 4 = CURIOUS
// 5 = AUTO
// ============================================================

bool setEmotion(int mood) {

  temporaryEmotion =
    false;


  // ==========================================================
  // AUTO MODE
  // ==========================================================

  if (mood == 5) {

    autoEmotion =
      true;


    currentMood =
      NORMAL;


    nextEmotionTime =
      millis() +
      random(
        4000,
        9000
      );


    Monitor.println(
      "Emotion: AUTO"
    );


    return true;
  }


  // ==========================================================
  // MANUAL MODE
  // ==========================================================

  autoEmotion =
    false;


  mood =
    constrain(
      mood,
      0,
      4
    );


  currentMood =
    (EyeMood)mood;


  lookCenter();


  Monitor.print(
    "Emotion: "
  );


  Monitor.println(
    mood
  );


  return true;
}


// ============================================================
// TEMPORARY EXPRESSION
//
// Used by Gemini for conversational reactions. When the expression expires,
// the face returns to its normal automatic behaviour rather than getting
// stuck in one mood.
// ============================================================

bool expressEmotion(
  int mood,
  int durationMs
) {

  mood = constrain(
    mood,
    0,
    4
  );

  durationMs = constrain(
    durationMs,
    500,
    5000
  );

  currentMood =
    (EyeMood)mood;

  autoEmotion = true;
  temporaryEmotion = true;

  emotionEndTime =
    millis() +
    durationMs;

  lookCenter();

  Monitor.print(
    "Expression: "
  );

  Monitor.println(
    mood
  );

  return true;
}


// ============================================================
// WEB BLINK
// ============================================================

bool webBlink() {

  startBlink();

  return true;
}


// ============================================================
// AUTO EMOTIONS
// ============================================================

void updateAutoEmotion() {

  if (!autoEmotion) {
    return;
  }


  unsigned long now =
    millis();


  // ==========================================================
  // RETURN TO NORMAL
  // ==========================================================

  if (
    temporaryEmotion &&
    now >= emotionEndTime
  ) {

    currentMood =
      NORMAL;


    temporaryEmotion =
      false;


    nextEmotionTime =
      now +
      random(
        5000,
        12000
      );
  }


  // ==========================================================
  // START RANDOM EMOTION
  // ==========================================================

  if (
    !temporaryEmotion &&
    now >= nextEmotionTime
  ) {

    int randomMood =
      random(
        1,
        5
      );


    currentMood =
      (EyeMood)randomMood;


    temporaryEmotion =
      true;


    emotionEndTime =
      now +
      random(
        800,
        1800
      );
  }
}


// ============================================================
// RANDOM BLINK HANDLING
// ============================================================

void updateRandomBlink() {

  unsigned long now =
    millis();


  // ==========================================================
  // SECOND BLINK
  // ==========================================================

  if (
    waitingSecondBlink &&
    blinkState ==
      BLINK_IDLE &&
    now >= secondBlinkAt
  ) {

    startBlink();


    waitingSecondBlink =
      false;


    return;
  }


  // ==========================================================
  // NORMAL RANDOM BLINK
  // ==========================================================

  if (
    blinkState ==
      BLINK_IDLE &&
    !waitingSecondBlink &&
    now - lastBlink >
      nextBlink
  ) {

    startBlink();


    lastBlink =
      now;


    nextBlink =
      random(
        2500,
        6500
      );


    // Approximately 20% chance
    // of a second quick blink.

    if (
      random(
        0,
        5
      ) == 0
    ) {

      waitingSecondBlink =
        true;


      secondBlinkAt =
        now +
        random(
          230,
          330
        );
    }
  }
}


// ============================================================
// SETUP
// ============================================================

void setup() {

  // ==========================================================
  // MOTOR PINS
  // ==========================================================

  pinMode(
    ENA,
    OUTPUT
  );


  pinMode(
    IN1,
    OUTPUT
  );


  pinMode(
    IN2,
    OUTPUT
  );


  pinMode(
    IN3,
    OUTPUT
  );


  pinMode(
    IN4,
    OUTPUT
  );


  pinMode(
    ENB,
    OUTPUT
  );


  stopMotors();


  // ==========================================================
  // SERVO
  // ==========================================================

  headServo.attach(
    SERVO_PIN
  );


  headServo.write(
    90
  );


  // ==========================================================
  // OLED
  // ==========================================================

  u8g2.begin();


  // ==========================================================
  // RANDOM
  // ==========================================================

  randomSeed(
    micros()
  );


  // ==========================================================
  // INITIAL EYES
  // ==========================================================

  eyeX =
    0;


  eyeY =
    0;


  targetX =
    0;


  targetY =
    0;


  openness =
    1.0;


  targetOpenness =
    1.0;


  currentMood =
    NORMAL;


  drawEyes();


  // ==========================================================
  // BRIDGE
  // ==========================================================

  Bridge.begin();


  Bridge.provide(
    "drive",
    drive
  );


  Bridge.provide(
    "move_timed",
    moveTimed
  );


  Bridge.provide(
    "stop",
    stopMotors
  );


  Bridge.provide(
    "speed",
    setSpeed
  );


  Bridge.provide(
    "servo",
    setServo
  );


  Bridge.provide(
    "emotion",
    setEmotion
  );


  Bridge.provide(
    "express",
    expressEmotion
  );


  Bridge.provide(
    "blink",
    webBlink
  );


  // ==========================================================
  // MONITOR
  // ==========================================================

  Monitor.begin(
    115200
  );


  Monitor.println(
    "UNO Q Robot Ready"
  );


  Monitor.println(
    "Motors + Servo + SH1106 Face"
  );


  delay(
    300
  );


  // Cute startup blink

  startBlink();
}


// ============================================================
// LOOP
// ============================================================

void loop() {

  unsigned long now =
    millis();


  // The timed-move deadline is checked on the MCU, independently of the web
  // server and Gemini process.
  if (
    timedMotionActive &&
    (long)(now - timedMotionStopAt) >= 0
  ) {

    stopMotors();

    Monitor.println(
      "Timed motion stopped"
    );
  }


  // ==========================================================
  // SMOOTH EYE MOVEMENT
  // ==========================================================

  updateEyeMovement();


  // ==========================================================
  // BLINK
  // ==========================================================

  updateBlink();


  // ==========================================================
  // RANDOM BLINK / DOUBLE BLINK
  // ==========================================================

  updateRandomBlink();


  // ==========================================================
  // RANDOM LOOK
  // ==========================================================

  if (
    now -
    lastLook >
    nextLook
  ) {

    randomLook();


    lastLook =
      now;


    nextLook =
      random(
        900,
        2600
      );
  }


  // ==========================================================
  // AUTO EMOTIONS
  // ==========================================================

  updateAutoEmotion();


  // ==========================================================
  // DRAW OLED
  // ==========================================================

  drawEyes();


  delay(
    15
  );
}
