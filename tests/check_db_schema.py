import psycopg2

conn = psycopg2.connect(
    host="localhost", port="5432",
    dbname="greenstep_db", user="postgres", password="edf2026"
)
cursor = conn.cursor()

# MERCHANT_SME 컬럼 확인
cursor.execute(
    "SELECT column_name, data_type FROM information_schema.columns "
    "WHERE table_name = 'merchant_sme' ORDER BY ordinal_position;"
)
print("=== MERCHANT_SME 컬럼 ===")
for row in cursor.fetchall():
    print(f"  {row[0]} ({row[1]})")

# main_category_vec 존재 여부
cursor.execute(
    "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name = 'main_category_vec');"
)
print(f"\n=== main_category_vec 존재: {cursor.fetchone()[0]} ===")

# middle_category_vec 존재 여부
cursor.execute(
    "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name = 'middle_category_vec');"
)
print(f"=== middle_category_vec 존재: {cursor.fetchone()[0]} ===")

# main_category_vec 컬럼 (존재 시)
cursor.execute(
    "SELECT column_name FROM information_schema.columns "
    "WHERE table_name = 'main_category_vec' ORDER BY ordinal_position;"
)
rows = cursor.fetchall()
if rows:
    print("\n=== main_category_vec 컬럼 ===")
    for row in rows:
        print(f"  {row[0]}")

cursor.close()
conn.close()
print("\nDone.")
