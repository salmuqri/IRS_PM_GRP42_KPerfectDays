
## SECTION 1 : K-Perfect Days
## Group Travel Itinerary Planner - Recommender System

<img src="Video/K_Perfect_Days_LandingPage.png"
     style="float: left; margin-right: 0px;" />

---

## SECTION 2 : EXECUTIVE SUMMARY 
**K Perfect Days** is an intelligent travel recommendation system built around a simple idea: a perfect trip should feel personal. Rather than recommending destinations based only on popularity, the system seeks to understand travelers their interests, preferred activities, travel styles, social connections, and evolving experiences to create recommendations that become more relevant over time.

The system brings together user understanding, AI-driven travel personas, Knowledge Graphs, recommendation models, and contextual reasoning to connect travelers with activities and places that fit them. It is designed not only to recommend, but to continuously learn as users interact with the product.

Behind K Perfect Days is a team that transformed an ambitious concept into a working end-to-end system within a highly constrained timeframe. Each member brought a different perspective and technical strength, from data and AI to recommendation, reasoning, and system development. The result reflects extensive experimentation, problem-solving, iteration, and collaboration.

More than a recommender, **K Perfect Days represents the team’s shared effort to explore what a truly adaptive and intelligent travel experience could become.**


---

## SECTION 3 : PROJECT CONTRIBUTION

| Official Full Name  | Student ID  | Email
| :------------ |:---------------:| :-----|
| Nadia Alolyani | A0354462A | nalolyan@u.nus.edu
| Sarah Almuqri | A0362910J | almuqri@u.nus.edu
| Abdullah Alhussaini | A0352097Y | Abdullah.Alhussaini@u.nus.edu

---

## SECTION 4 : VIDEO OF SYSTEM MODELLING & USE CASE DEMO

[![Sudoku AI Solver](http://img.youtube.com/vi/-AiYLUjP6o8/0.jpg)](https://youtu.be/-AiYLUjP6o8 "Sudoku AI Solver")

Note: It is not mandatory for every project member to appear in video presentation; Presentation by one project member is acceptable. 
More reference video presentations [here](https://telescopeuser.wordpress.com/2018/03/31/master-of-technology-solution-know-how-video-index-2/ "video presentations")

---

## SECTION 5 : USER GUIDE

`Refer to appendix <Installation & User Guide> in project report at Github Folder: ProjectReport`

### [ 1 ] Requirements and configuration

Use Python 3.11 or 3.12 and a Neo4j database populated with the appropriate user, persona and place graph. The planner also needs an OpenAI API key and an openrouteservice key. Geoapify is optional; Open-Meteo does not require a personal key for the tested usage. API accounts may incur charges.

From the extracted repository root, create a virtual environment and install the listed dependencies:

> python3 -m venv .venv

> source .venv/bin/activate

> python -m pip install -r scripts/requirements.txt


Create frontend/.env using your own credentials and the following variable names. Never commit real keys or passwords to GitHub:

NEO4J_URI=neo4j+s://YOUR_AURA_HOST

NEO4J_USERNAME=neo4j

NEO4J_PASSWORD=YOUR_PASSWORD

NEO4J_DATABASE=neo4j

OPENAI_API_KEY=YOUR_OPENAI_KEY

ORS_API_KEY=YOUR_ORS_KEY

GEOAPIFY_API_KEY=OPTIONAL_KEY



### [ 2 ] Start and use the system

### Install additional necessary libraries. 

1. Run `python scripts/server.py` from the repository root.
2. Open http://127.0.0.1:8000/frontend/ in a browser.
3. Sign up or log in, choose activities, and explore the Discover recommendations.
4. Optionally create a shared trip, invite other users, and collect votes.
5. In Plan a trip, type a complete request with city, travel dates or number of nights, preferences and hard exclusions.
6. Select Generate itinerary, review the day-by-day result, and check warnings about unverified hours.


---
## SECTION 6 : PROJECT REPORT / PAPER

`Refer to project report at Github Folder: ProjectReport`

**Recommended Sections for Project Report / Paper:**
- Executive Summary
- Business Case and Market Research
- System Design and Model
- System Development and Implementation
- Experiments and Results
- Findings and Discussion
- References
- Appendix A. Mapping the System to IRS Techniques
- Appendix B. Installation and User Guide
- Appendix C. Original Project Proposal

---
## SECTION 7 : MISCELLANEOUS

`Refer to Github Folder: Miscellaneous`



