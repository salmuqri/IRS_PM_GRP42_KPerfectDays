from neo4j import GraphDatabase

URI = "bolt+ssc://dfe7760e.databases.neo4j.io"
USERNAME = "neo4j"
PASSWORD = "Eh7KZ0gZA08LvwJqHgnWiAgS4vJPSRRWd5Jzzq1KPH0"

driver = GraphDatabase.driver(
    URI,
    auth=(USERNAME, PASSWORD)
)


try:
    driver.verify_connectivity()
    print("1. CONNECTION SUCCESSFUL")

    with driver.session() as session:
        result = session.run("RETURN 1 AS test")
        print("2. QUERY SUCCESSFUL:", result.single()["test"])

        result = session.run("""
            MERGE (u:User {profileId: "P999999"})
            RETURN u.profileId AS profileId
        """)

        print("3. USER CREATED:", result.single()["profileId"])

except Exception as e:
    print("NEO4J ERROR:")
    print(type(e).__name__)
    print(e)

finally:
    driver.close()