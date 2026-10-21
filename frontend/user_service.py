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

    # ========================================================
    # INITIALIZATION
    # ========================================================

    def __init__(self):

        self.uri = os.environ["NEO4J_URI"]
        self.username = os.environ["NEO4J_USERNAME"]
        self.password = os.environ["NEO4J_PASSWORD"]

        self.database = os.environ.get(
            "NEO4J_DATABASE",
            "neo4j"
        )

        self.driver = GraphDatabase.driver(
            self.uri,
            auth=(
                self.username,
                self.password
            )
        )

    # ========================================================
    # USER
    # ========================================================

    def merge_user(
        self,
        profile_id,
        name,
        email
    ):

        query = """
        MERGE (u:User {
            profileId: $profile_id
        })

        SET
            u.name = $name,
            u.email = $email

        RETURN
            properties(u) AS user
        """

        with self.driver.session(
            database=self.database
        ) as session:

            record = session.run(
                query,
                profile_id=profile_id,
                name=name,
                email=email
            ).single()

            return (
                dict(record)
                if record
                else None
            )

    # ========================================================
    # INITIAL ACTIVITIES
    #
    # These are selected during onboarding.
    #
    # IMPORTANT:
    #
    # Activity.confidence
    #     = shared/original KG value
    #
    # HAS_ACTIVITY.confidence
    #     = user-specific dynamic value
    #
    # We modify ONLY the relationship confidence.
    # ========================================================

    def add_activities(
        self,
        profile_id,
        activities
    ):

        query = """
        MATCH (u:User {
            profileId: $profile_id
        })

        UNWIND
            $activities AS activity_name

        MATCH (a:Activity {
            activity: activity_name
        })

        MERGE
            (u)-[r:HAS_ACTIVITY]->(a)

        ON CREATE SET
            r.confidence = 1.0,
            r.preferenceStrength = "high",
            r.sourceType = "explicit",
            r.evidence =
                "Selected in good-day questionnaire",
            r.behavioralLikes = 0,
            r.behavioralDislikes = 0

        RETURN
            collect(
                DISTINCT a.activity
            ) AS activities
        """

        with self.driver.session(
            database=self.database
        ) as session:

            record = session.run(
                query,
                profile_id=profile_id,
                activities=activities
            ).single()

            return (
                dict(record)
                if record
                else None
            )

    # ========================================================
    # PLACE FEEDBACK
    #
    # Multi-hop path:
    #
    # Place_
    #    |
    #    | HAS_CATEGORY
    #    v
    # Category
    #    |
    #    | ACTIVITY_SIMILARITY
    #    v
    # P_Activity
    #
    # Then:
    #
    # P_Activity.activityType
    #          ==
    # Activity.activity
    #
    # ========================================================

    def update_preference_from_place(
        self,
        profile_id,
        place_id,
        feedback
    ):

        # ====================================================
        # LIKE
        #
        # Existing user activity:
        #     confidence + 0.1
        #
        # New activity learned from behavior:
        #     confidence = 0.6
        #
        # Maximum:
        #     1.0
        # ====================================================

        if feedback == "like":

            query = """
            MATCH (u:User {
                profileId: $profile_id
            })

            MATCH
                (place:Place_ {
                    placeId: $place_id
                })
                -[:HAS_CATEGORY]->
                (category:Category)
                -[:ACTIVITY_SIMILARITY]->
                (pa:P_Activity)

            MATCH (a:Activity)

            WHERE
                a.activity = pa.activityType

            MERGE
                (u)-[r:HAS_ACTIVITY]->(a)

            ON CREATE SET
                r.confidence = 0.6,
                r.preferenceStrength = "medium",
                r.sourceType = "behavioral",
                r.evidence =
                    "Learned from liked place",
                r.behavioralLikes = 1,
                r.behavioralDislikes = 0

            ON MATCH SET

                r.confidence =
                    CASE
                        WHEN
                            coalesce(
                                r.confidence,
                                0.5
                            ) + 0.1 > 1.0
                        THEN
                            1.0
                        ELSE
                            coalesce(
                                r.confidence,
                                0.5
                            ) + 0.1
                    END,

                r.behavioralLikes =
                    coalesce(
                        r.behavioralLikes,
                        0
                    ) + 1

            RETURN DISTINCT

                place.name
                    AS place,

                category.category
                    AS category,

                pa.activityType
                    AS personaActivity,

                a.activity
                    AS activity,

                r.confidence
                    AS userConfidence,

                r.behavioralLikes
                    AS behavioralLikes,

                r.behavioralDislikes
                    AS behavioralDislikes
            """

        # ====================================================
        # DISLIKE
        #
        # Existing user activity:
        #     confidence - 0.2
        #
        # Minimum:
        #     0.0
        #
        # A dislike does NOT create a new activity.
        # ====================================================

        elif feedback == "dislike":

            query = """
            MATCH (u:User {
                profileId: $profile_id
            })

            MATCH
                (place:Place_ {
                    placeId: $place_id
                })
                -[:HAS_CATEGORY]->
                (category:Category)
                -[:ACTIVITY_SIMILARITY]->
                (pa:P_Activity)

            MATCH
                (u)-[r:HAS_ACTIVITY]->
                (a:Activity)

            WHERE
                a.activity = pa.activityType

            SET

                r.confidence =
                    CASE
                        WHEN
                            coalesce(
                                r.confidence,
                                0.5
                            ) - 0.2 < 0.0
                        THEN
                            0.0
                        ELSE
                            coalesce(
                                r.confidence,
                                0.5
                            ) - 0.2
                    END,

                r.behavioralDislikes =
                    coalesce(
                        r.behavioralDislikes,
                        0
                    ) + 1

            RETURN DISTINCT

                place.name
                    AS place,

                category.category
                    AS category,

                pa.activityType
                    AS personaActivity,

                a.activity
                    AS activity,

                r.confidence
                    AS userConfidence,

                r.behavioralLikes
                    AS behavioralLikes,

                r.behavioralDislikes
                    AS behavioralDislikes
            """

        # ====================================================
        # INVALID FEEDBACK
        # ====================================================

        else:

            raise ValueError(
                "Feedback must be 'like' or 'dislike'."
            )

        # ====================================================
        # EXECUTE FEEDBACK QUERY
        # ====================================================

        with self.driver.session(
            database=self.database
        ) as session:

            result = session.run(
                query,
                profile_id=profile_id,
                place_id=place_id
            )

            return [
                dict(record)
                for record in result
            ]

    # ========================================================
    # PERSONA MAPPING
    #
    # Similarity:
    #
    # User HAS_ACTIVITY confidence
    #               ×
    # Persona P_Activity weight
    #               ↓
    #         Persona score
    #
    #
    # CRITICAL INVARIANT:
    #
    # One User can have ONLY ONE SIMILAR_TO relationship.
    #
    #
    # Every execution:
    #
    # 1. Score all Personas
    # 2. Select one winner
    # 3. Delete ALL existing SIMILAR_TO relationships
    # 4. MERGE exactly one SIMILAR_TO
    #
    # ========================================================

    def map_persona(
        self,
        profile_id
    ):

        # ====================================================
        # Everything happens in ONE Neo4j write transaction.
        # ====================================================

        def replace_persona(tx):

            # =================================================
            # STEP 1
            # SCORE ALL PERSONAS
            # =================================================

            best = tx.run(
                """
                MATCH (u:User {
                    profileId: $profile_id
                })

                MATCH
                    (u)-[ur:HAS_ACTIVITY]->
                    (a:Activity)

                MATCH
                    (p:Persona)
                    -[:HAS_ACTIVITY]->
                    (pa:P_Activity)

                WHERE
                    a.activity = pa.activityType

                WITH
                    p,

                    sum(
                        coalesce(
                            ur.confidence,
                            0.5
                        )
                        *
                        coalesce(
                            pa.weight,
                            1.0
                        )
                    ) AS personaScore,

                    count(
                        DISTINCT a.activity
                    ) AS matchingActivities

                ORDER BY
                    personaScore DESC,
                    matchingActivities DESC,
                    elementId(p)

                LIMIT 1

                RETURN
                    elementId(p)
                        AS personaElementId,

                    properties(p)
                        AS persona,

                    personaScore,

                    matchingActivities
                """,

                profile_id=profile_id

            ).single()

            # =================================================
            # NO PERSONA COULD BE MATCHED
            # =================================================

            if not best:
                return None

            # =================================================
            # STEP 2
            # DELETE EVERY EXISTING SIMILAR_TO
            #
            # Example:
            #
            # User -> Persona 4
            # User -> Persona 4
            # User -> Persona 2
            #
            # ALL THREE ARE DELETED.
            # =================================================

            tx.run(
                """
                MATCH (u:User {
                    profileId: $profile_id
                })

                OPTIONAL MATCH
                    (u)-[old:SIMILAR_TO]->
                    (:Persona)

                WITH
                    u,
                    collect(old)
                        AS oldRelationships

                FOREACH (
                    relationship
                    IN oldRelationships |

                    DELETE relationship
                )

                RETURN
                    u.profileId AS profileId
                """,

                profile_id=profile_id

            ).consume()

            # =================================================
            # STEP 3
            # MERGE EXACTLY ONE WINNING PERSONA
            #
            # MERGE is intentional.
            #
            # We do NOT use CREATE here.
            # =================================================

            mapped = tx.run(
                """
                MATCH (u:User {
                    profileId: $profile_id
                })

                MATCH (p:Persona)

                WHERE
                    elementId(p)
                    =
                    $persona_element_id

                MERGE
                    (u)-[r:SIMILAR_TO]->(p)

                SET
                    r.score =
                        $persona_score,

                    r.matchingActivities =
                        $matching_activities

                RETURN
                    properties(p)
                        AS persona,

                    r.score
                        AS personaScore,

                    r.matchingActivities
                        AS matchingActivities
                """,

                profile_id=profile_id,

                persona_element_id=best[
                    "personaElementId"
                ],

                persona_score=best[
                    "personaScore"
                ],

                matching_activities=best[
                    "matchingActivities"
                ]

            ).single()

            return (
                dict(mapped)
                if mapped
                else None
            )

        # ====================================================
        # EXECUTE PERSONA TRANSACTION
        # ====================================================

        with self.driver.session(
            database=self.database
        ) as session:

            return session.execute_write(
                replace_persona
            )

    # ========================================================
    # DEBUG / VERIFY USER MODEL
    #
    # ========================================================

    def get_user_model(
        self,
        profile_id
    ):

        query = """
        MATCH (u:User {
            profileId: $profile_id
        })

        OPTIONAL MATCH
            (u)-[ar:HAS_ACTIVITY]->
            (a:Activity)

        WITH
            u,

            collect(
                DISTINCT {
                    activity:
                        a.activity,

                    originalNodeConfidence:
                        a.confidence,

                    userConfidence:
                        ar.confidence,

                    likes:
                        ar.behavioralLikes,

                    dislikes:
                        ar.behavioralDislikes
                }
            ) AS activities

        OPTIONAL MATCH
            (u)-[sr:SIMILAR_TO]->
            (p:Persona)

        RETURN
            u.profileId
                AS profileId,

            activities,

            count(
                sr
            ) AS personaCount,

            collect(
                {
                    persona:
                        properties(p),

                    score:
                        sr.score,

                    matchingActivities:
                        sr.matchingActivities
                }
            ) AS personas
        """

        with self.driver.session(
            database=self.database
        ) as session:

            record = session.run(
                query,
                profile_id=profile_id
            ).single()

            return (
                dict(record)
                if record
                else None
            )

            
    def add_friend(
        self,
        original_profile_id,
        new_profile_id
    ):

        # Do not create friendship with yourself
        if original_profile_id == new_profile_id:
            return None

        query = """
        MATCH (original:User {
            profileId: $original_profile_id
        })

        MATCH (new:User {
            profileId: $new_profile_id
        })

        MERGE
            (original)-[r:HAS_FRIEND]->(new)

        ON CREATE SET
            r.createdAt = datetime()

        RETURN
            original.profileId AS originalUser,
            new.profileId AS friend,
            type(r) AS relationship
        """

        with self.driver.session(
            database=self.database
        ) as session:

            record = session.run(
                query,
                original_profile_id=original_profile_id,
                new_profile_id=new_profile_id
            ).single()

            return (
                dict(record)
                if record
                else None
            )
    # ========================================================
    # CLOSE DRIVER
    # ========================================================

    def close(self):

        self.driver.close()
