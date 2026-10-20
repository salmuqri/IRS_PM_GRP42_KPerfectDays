# -*- coding: utf-8 -*-

import os
from neo4j import GraphDatabase

# ============================================================
# NEO4J CONFIG
# ============================================================

os.environ["NEO4J_URI"] = "bolt+ssc://dfe7760e.databases.neo4j.io"
os.environ["NEO4J_USERNAME"] = "neo4j"
os.environ["NEO4J_PASSWORD"] = "Eh7KZ0gZA08LvwJqHgnWiAgS4vJPSRRWd5Jzzq1KPH0"
os.environ["NEO4J_DATABASE"] = "neo4j"



class UserService:


    def __init__(self):
            
            self.uri = os.environ["NEO4J_URI"]
            self.username = os.environ["NEO4J_USERNAME"]
            self.password = os.environ["NEO4J_PASSWORD"]
            self.database = os.environ["NEO4J_DATABASE"]

            self.driver = GraphDatabase.driver(
               os.environ["NEO4J_URI"],
            auth=(
                os.environ["NEO4J_USERNAME"],
                os.environ["NEO4J_PASSWORD"]
                )
            )


    def merge_user(self, profile_id, name, email):

        query = """
        MERGE (u:User {profileId: $profile_id})
        SET u.name = $name,
            u.email = $email
        RETURN u
        """

        with self.driver.session(
            database=self.database
        ) as session:

            result = session.run(
                query,
                profile_id=profile_id,
                name=name,
                email=email
            )

            return result.single()


    def add_activities(self, profile_id, activities):

        query = """
        MATCH (u:User {profileId: $profile_id})

        UNWIND $activities AS activity_name

        MATCH (a:Activity {activity: activity_name})

        MERGE (u)-[r:HAS_ACTIVITY]->(a)

        SET r.confidence = 1.0,
            r.preferenceStrength = "high",
            r.sourceType = "explicit",
            r.evidence = "Selected during onboarding questionnaire"

        RETURN collect(a.activity) AS activities
        """

        with self.driver.session(database=self.database) as session:
            result = session.run(
                query,
                profile_id=profile_id,
                activities=activities
            )

            return result.single()

    def add_interest(self, profile_id, interest):

        query = """
        MATCH (u:User {profileId: $profile_id})
        MATCH (i:Interest {interest: $interest})

        MERGE (u)-[r:HAS_INTEREST]->(i)

        SET r.confidence = 1.0,
            r.preferenceStrength = "high",
            r.sourceType = "explicit",
            r.evidence = "User liked this interest"

        RETURN
            u.profileId AS user,
            i.interest AS interest
        """

        with self.driver.session(database=self.database) as session:
            result = session.run(
                query,
                profile_id=profile_id,
                interest=interest
            )

            return result.single()
        
    def remove_interest(self, profile_id, interest):

        query = """
        MATCH (u:User {profileId: $profile_id})
        MATCH (i:Interest {interest: $interest})
        MATCH (u)-[r:HAS_INTEREST]->(i)

        DELETE r

        RETURN
            u.profileId AS user,
            i.interest AS interest
        """

        with self.driver.session(database=self.database) as session:
            result = session.run(
                query,
                profile_id=profile_id,
                interest=interest
            )

            return result.single()


    def map_persona(self, profile_id):
        query = """
        MATCH (u:User {profileId: $profile_id})
        MATCH (u)-[:HAS_ACTIVITY]->(a:Activity)

        MATCH (p:Persona)-[:HAS_ACTIVITY]->(pa:P_Activity)

        WHERE a.activity = pa.activityType

        WITH
            u,
            p,
            count(DISTINCT a.activity) AS matchingActivities

        ORDER BY matchingActivities DESC

        WITH
            u,
            p,
            matchingActivities

        LIMIT 1

        MERGE (u)-[r:SIMILAR_TO]->(p)

        RETURN
            u,
            r,
            p,
            matchingActivities
        """

        with self.driver.session(
            database=self.database
        ) as session:

            result = session.run(
                query,
                profile_id=profile_id
            )

            return result.single()

    
    
    def close(self):
        self.driver.close()