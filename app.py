from flask import Flask, render_template, request, session, redirect, url_for
from flask import render_template_string
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
import google.generativeai as palm
import markdown
from markdown.extensions.fenced_code import FencedCodeExtension
import re
import os
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import openai  # don't go for upgraded version
import json
import requests
from flask_weasyprint import HTML, render_pdf
from weasyprint import CSS
from dotenv import load_dotenv
import google.generativeai as genai  # best practice name
import google.api_core.exceptions as palm_exceptions  # Make sure this is at the top
import random
import time
from google.api_core.exceptions import ResourceExhausted

model = genai.GenerativeModel('gemini-1.5-flash-002')
load_dotenv()  # Automatically loads .env from the project root

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv("SECRET_KEY")
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
openai.api_key = os.getenv("OPENAI_API_KEY")
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))

# Create a Markdown instance with the FencedCodeExtension
md = markdown.Markdown(extensions=[FencedCodeExtension()])



class Course(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    course_name = db.Column(db.String(100), nullable=False)
    content = db.Column(db.Text, nullable=False)




class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(20), unique = True)
    email = db.Column(db.String(50), unique = True)
    password = db.Column(db.String(80))
    courses = db.relationship('Course', backref='user', lazy=True)
    date_joined = db.Column(db.DateTime, default=datetime.now)




@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))




@app.route("/quiz_interface")
def quiz_interface():
    return render_template("home.html")





@app.route("/quiz", methods=["GET", "POST"])
def quiz():
    if request.method == "POST":
        print(request.form)  # Debugging form data

        language = request.form["language"]
        questions = request.form["ques"]
        choices = request.form["choices"]

        # Add randomness to the prompt
        random_hint = f"(Seed: {random.randint(1000, 9999)}, Time: {datetime.now()})"  # Use datetime directly

        prompt = f"""
        Create a quiz on the topic: {language}.
        Generate {questions} questions, each with {choices} multiple choice options.
        Try to make the quiz different each time, even for the same topic. {random_hint}
        Return the quiz in JSON format like this:
        {{
          "topic": "Topic Name",
          "questions": [
            {{
              "question": "What is ...?",
              "choices": ["A", "B", "C", "D"],
              "answer": "B"
            }},
            ...
          ]
        }}
        """

        try:
            response = model.generate_content(prompt)

            # Handle Gemini's markdown response wrapping
            text_response = response.text.strip()

            if text_response.startswith("```json"):
                text_response = text_response[7:].strip()
            if text_response.endswith("```"):
                text_response = text_response[:-3].strip()

            quiz_content = json.loads(text_response)  # Proper JSON parsing

            session['response'] = quiz_content  # Save quiz data in session

            return render_template("quiz.html", quiz_content=quiz_content)

        except Exception as e:
            return f"<p>Error generating quiz: {str(e)}</p>"

    elif request.method == "GET":
        print("GET request received")  # Debugging statement
        score = 0
        actual_answers = []
        given_answers = []

        # Extract answers from query parameters
        for key in sorted(request.args.keys()):
            if key.startswith('question_'):
                given_answers.append(request.args[key].strip())  # Strip whitespace

        res = session.get('response', None)
        if not res:
            return "<p>No quiz data found. Please generate a quiz first.</p>"

        for answer in res["questions"]:
            actual_answers.append(answer["answer"].strip())  # Strip whitespace

    # Debugging output
        print("Given Answers:", given_answers)
        print("Actual Answers:", actual_answers)

    # Compare answers and calculate score
        if given_answers:
            for i in range(min(len(actual_answers), len(given_answers))):
            # Extract the letter from the given answer (e.g., 'B) Firewall' -> 'B')
                given_answer_letter = given_answers[i].split('.')[0].strip()  # Get the part before the closing parenthesis and strip whitespace
                if actual_answers[i].lower() == given_answer_letter.lower():  # Case insensitive comparison
                    score += 1

    # Debugging output
        print("Calculated Score:", score)

    # Return the score and answers to the score template
        return render_template("score.html", actual_answers=actual_answers, given_answers=given_answers, score=score)

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        hashed_password = generate_password_hash(request.form['password'], method='pbkdf2:sha256')
        new_user = User(username=request.form['username'], email=request.form['email'], password=hashed_password)
        db.session.add(new_user)
        db.session.commit()
        return redirect(url_for('login'))
    return render_template('signup.html')




@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(email=request.form['email']).first()
        if user and check_password_hash(user.password, request.form['password']):
            login_user(user)
            return redirect(url_for('dashboard'))
    return render_template('login.html')




@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))




@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.is_authenticated:
        return render_template('dashboard.html', user=current_user)
    else:
        return redirect(url_for('login'))





@app.route('/')
def home():
    if current_user.is_authenticated:
        saved_courses = Course.query.filter_by(user_id=current_user.id).all()
        recommended_courses = generate_recommendations(saved_courses)
        return render_template('app.html', saved_courses=saved_courses, recommended_courses = recommended_courses, user=current_user)
    else:
        return redirect(url_for('login'))




@app.route('/course', methods=['GET', 'POST'])
@login_required
def course():
    if request.method == 'POST':
        course_name = request.form['course_name']
        completions = generate_text(course_name)
        print(f"course_name: {course_name}")
        rendered = render_template('courses/course1.html', completions=completions, course_name=course_name)
        new_course = Course(course_name=course_name, content=rendered, user_id=current_user.id)
        db.session.add(new_course)
        db.session.commit()
        return rendered
    return render_template('courses/course1.html')




@app.route('/r_course/<course_name>', methods=['GET', 'POST'])
@login_required
def r_course(course_name):
    completions = None  # Initialize completions to None
    if request.method == 'POST':
        completions = generate_text(course_name)
        print(f"course_name: {course_name}")
        rendered = render_template('courses/course1.html', completions=completions, course_name=course_name)
        new_course = Course(course_name=course_name, content=rendered, user_id=current_user.id)
        db.session.add(new_course)
        db.session.commit()
        return rendered
    # If the request method is 'GET', generate the text for the course
    completions = generate_text(course_name)
    return render_template('courses/course1.html', completions=completions, course_name=course_name)


@app.route('/saved_course/<course_name>')
@login_required
def saved_course(course_name):
    course = Course.query.filter_by(course_name=course_name, user_id=current_user.id).first()
    if course is None:
        # If there is no course with the given name, redirect to the home page
        return "<p>Course not found</p>"
    else:
        # If a course with the given name exists, render a template and pass the course to it
        return render_template('courses/saved_course.html', course=course)




@app.route('/module/<course_name>/<module_name>', methods=['GET'])
def module(course_name,module_name):
    content = generate_module_content(course_name,module_name)
    if not content:
        return "<p>Module not found</p>"
    html = render_template('module.html', content=content)
    
    # If the 'download' query parameter is present in the URL, return the page as a PDF
    if 'download' in request.args:
        #Create a CSS object for the A3 page size
        a3_css = CSS(string='@page {size: A3; margin: 1cm;}')
        return render_pdf(HTML(string=html), stylesheets=[a3_css])

    # Otherwise, return the page as HTML
    return html 


@app.route('/app1')
def app1():
    if current_user.is_authenticated:
        saved_courses = Course.query.filter_by(user_id=current_user.id).all()
        recommended_courses = generate_recommendations(saved_courses)
        return render_template('app.html', saved_courses=saved_courses, recommended_courses = recommended_courses, user=current_user)
    else:
        return redirect(url_for('login'))





def markdown_to_list(markdown_string):
    # Split the string into lines
    lines = markdown_string.split('\n')
    # Use a regular expression to match lines that start with '* '
    list_items = [re.sub(r'\* ', '', line) for line in lines if line.startswith('* ')]
    return list_items




def generate_text(course):
    prompts = {
        'approach': f"You are a pedagogy expert and you are designing a learning material for {course}. Provide a high-level approach to teaching this course effectively, in markdown.",
        'modules': f"Based on the course {course}, provide a list of modules in markdown bullet points."
    }

    completions = {}
    for key, prompt in prompts.items():
        response = model.generate_content(
                prompt,
                generation_config={"temperature": 0.1, "max_output_tokens": 5000}
            )

        if key == 'modules':
            markdown_string = response.text.replace('•', '*') if response.text else ""
            completions[key] = markdown_to_list(markdown_string)
        else:
            completions[key] = markdown.markdown(response.text if response.text else "")
    
    return completions

def generate_module_content(course_name, module_name):
    try:
        model = genai.GenerativeModel('gemini-1.5-flash-002')

        prompts = {
            "module": f"Course Name: {course_name} Topic: {module_name}. Please provide a comprehensive explanation of {module_name}. Feel free to use examples or analogies to clarify complex ideas.",
            "code": f"Course Name: {course_name} Topic: {module_name}. If the explanation of {module_name} requires code snippets for better understanding, please provide the relevant code snippets.",
            "ascii": f"Course Name: {course_name} Topic: {module_name}. If the explanation of {module_name} requires diagram snippets for better understanding, please provide the relevant diagram snippets in the form of ASCII art."
        }

        results = {}
        for key, prompt in prompts.items():
            response = model.generate_content(
                prompt,
                generation_config={"temperature": 0.1, "max_output_tokens": 5000}
            )
            if response.candidates and response.candidates[0].content.parts:
                results[key] = response.candidates[0].content.parts[0].text

            else:
                print(f"⚠️ Skipped '{key}' content generation due to blocked output or missing parts.")
                results[key] = ""

        # Convert markdown content to HTML
        module_content_html = md.convert(results["module"])
        code_content_html = md.convert(results["code"])
        ascii_content_html = md.convert(results["ascii"])

        return f"{module_content_html}\n{code_content_html}\n{ascii_content_html}"

    except Exception as e:
        print(f"An error occurred while generating module content: {str(e)}")
        return f"An error occurred: {str(e)}"



def generate_recommendations(saved_courses):
    recommended_courses = []

    for course in saved_courses:
        prompt = f"Based on the course '{course.course_name}', suggest one next course to take with a short (max 70 character) description."

        response = model.generate_content(
                prompt,
                generation_config={"temperature": 0.1, "max_output_tokens": 5000}
            )
        if response.text:
            lines = response.text.strip().split('\n', 1)
            course_name = lines[0].strip()
            description = markdown.markdown(lines[1].strip()) if len(lines) > 1 else ""
            recommended_courses.append({'name': course_name, 'description': description})

    return recommended_courses


@app.route('/about')
def about():
    return render_template('about.html')


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(host="127.0.0.1", debug=True)

def generate_content_with_retry(model, content, retries=3):
    for attempt in range(retries):
        try:
            response = model.generate_content(content)
            return response
        except ResourceExhausted as e:
            print(f"Quota exceeded: {e}. Retrying in {33} seconds...")
            time.sleep(33)  # Wait for the retry delay
    raise Exception("Max retries exceeded")