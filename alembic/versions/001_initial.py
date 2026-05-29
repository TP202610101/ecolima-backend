"""Initial migration with PostGIS, all tables and indexes.

Revision ID: 001_initial
Revises: 
Create Date: 2026-05-27 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from geoalchemy2 import Geometry

# revision identifiers, used by Alembic.
revision = "001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    op.create_table(
        "users",
        sa.Column("user_id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("hashed_password", sa.String(), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=True),
        sa.Column("role", sa.String(length=20), nullable=False, server_default="analista"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "districts",
        sa.Column("district_id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("district_name", sa.String(length=100), nullable=False),
        sa.Column("geometry", Geometry("POLYGON", srid=4326), nullable=False),
        sa.Column("area_km2", sa.Float(), nullable=True),
        sa.Column("province", sa.String(length=100), nullable=True),
        sa.Column("region", sa.String(length=100), nullable=True),
    )
    op.create_index("ix_districts_geometry", "districts", ["geometry"], postgresql_using="gist")

    op.create_table(
        "socioeconomic_indicators",
        sa.Column("soc_id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("district_id", sa.Integer(), sa.ForeignKey("districts.district_id", ondelete="CASCADE"), nullable=False),
        sa.Column("year", sa.SmallInteger(), nullable=False),
        sa.Column("population_density", sa.Float(), nullable=False),
        sa.Column("total_population", sa.Integer(), nullable=False),
        sa.Column("income_stratum", sa.SmallInteger(), nullable=False),
        sa.Column("pct_nse_ab", sa.Float(), nullable=False),
        sa.Column("pct_nse_c", sa.Float(), nullable=False),
        sa.Column("pct_nse_de", sa.Float(), nullable=False),
        sa.Column("edu_level_head", sa.Float(), nullable=True),
        sa.Column("household_size_avg", sa.Float(), nullable=False),
        sa.Column("fuel_expenditure_sol", sa.Float(), nullable=True),
        sa.Column("pct_formal_employment", sa.Float(), nullable=True),
        sa.Column("pct_internet_access", sa.Float(), nullable=True),
        sa.Column("housing_type_apt_pct", sa.Float(), nullable=True),
        sa.Column("hacinamiento_idx", sa.Float(), nullable=True),
        sa.Column("pct_poverty", sa.Float(), nullable=True),
    )
    op.create_index("ix_socioeconomic_indicators_district_id", "socioeconomic_indicators", ["district_id"])

    op.create_table(
        "waste_generation",
        sa.Column("waste_id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("district_id", sa.Integer(), sa.ForeignKey("districts.district_id", ondelete="CASCADE"), nullable=False),
        sa.Column("year", sa.SmallInteger(), nullable=False),
        sa.Column("gpc_kg_per_capita_day", sa.Float(), nullable=False),
        sa.Column("pct_recyclable", sa.Float(), nullable=True),
        sa.Column("pct_organic", sa.Float(), nullable=True),
        sa.Column("pct_plastic", sa.Float(), nullable=True),
        sa.Column("pct_paper_cardboard", sa.Float(), nullable=True),
        sa.Column("pct_glass", sa.Float(), nullable=True),
        sa.Column("pct_metal", sa.Float(), nullable=True),
        sa.Column("collection_coverage_pct", sa.Float(), nullable=True),
        sa.Column("informal_recyclers_count", sa.Integer(), nullable=True),
        sa.Column("total_waste_tons_year", sa.Float(), nullable=True),
    )
    op.create_index("ix_waste_generation_district_id", "waste_generation", ["district_id"])

    op.create_table(
        "recycling_points",
        sa.Column("point_id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("district_id", sa.Integer(), sa.ForeignKey("districts.district_id", ondelete="CASCADE"), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("geometry", Geometry("POINT", srid=4326), nullable=False),
        sa.Column("point_type", sa.String(length=50), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("operator", sa.String(length=100), nullable=True),
        sa.Column("materials_accepted", sa.Text(), nullable=True),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("ix_recycling_points_district_id", "recycling_points", ["district_id"])
    op.create_index("ix_recycling_points_geometry", "recycling_points", ["geometry"], postgresql_using="gist")

    op.create_table(
        "candidate_zones",
        sa.Column("zone_id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("centroid_lat", sa.Float(), nullable=False),
        sa.Column("centroid_lon", sa.Float(), nullable=False),
        sa.Column("geometry", Geometry("POLYGON", srid=4326), nullable=False),
        sa.Column("district_id", sa.Integer(), sa.ForeignKey("districts.district_id", ondelete="CASCADE"), nullable=False),
        sa.Column("cell_area_km2", sa.Float(), nullable=False),
        sa.Column("population_density", sa.Float(), nullable=True),
        sa.Column("income_stratum", sa.SmallInteger(), nullable=True),
        sa.Column("fuel_expenditure_sol", sa.Float(), nullable=True),
        sa.Column("edu_level_head", sa.Float(), nullable=True),
        sa.Column("household_size_avg", sa.Float(), nullable=True),
        sa.Column("pct_nse_ab", sa.Float(), nullable=True),
        sa.Column("pct_nse_de", sa.Float(), nullable=True),
        sa.Column("gpc_kg_per_capita_day", sa.Float(), nullable=True),
        sa.Column("pct_recyclable", sa.Float(), nullable=True),
        sa.Column("pct_plastic", sa.Float(), nullable=True),
        sa.Column("informal_recyclers_count", sa.Integer(), nullable=True),
        sa.Column("road_density", sa.Float(), nullable=True),
        sa.Column("dist_to_main_road_m", sa.Float(), nullable=True),
        sa.Column("dist_to_market_m", sa.Float(), nullable=True),
        sa.Column("dist_to_nearest_point_m", sa.Float(), nullable=True),
        sa.Column("existing_points_500m", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("has_park_300m", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("slope_pct", sa.Float(), nullable=True),
        sa.Column("urbanized_area_pct", sa.Float(), nullable=True),
        sa.Column("recycling_potential_index", sa.Float(), nullable=True),
        sa.Column("is_suitable", sa.SmallInteger(), nullable=True),
        sa.Column("ml_score", sa.Float(), nullable=True),
        sa.Column("is_recommended", sa.Boolean(), nullable=True),
        sa.Column("priority_label", sa.String(length=10), nullable=True),
        sa.Column("recommendation_reason", sa.Text(), nullable=True),
        sa.Column("coverage_gap_m", sa.Float(), nullable=True),
        sa.Column("model_version", sa.String(length=20), nullable=True),
        sa.Column("inference_date", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_candidate_zones_district_id", "candidate_zones", ["district_id"])
    op.create_index("ix_candidate_zones_priority_label", "candidate_zones", ["priority_label"])
    op.create_index("ix_candidate_zones_is_recommended", "candidate_zones", ["is_recommended"])
    op.create_index("ix_candidate_zones_is_suitable", "candidate_zones", ["is_suitable"])

    op.create_table(
        "datasets",
        sa.Column("dataset_id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=True),
        sa.Column("uploaded_by", sa.Integer(), sa.ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("file_size_bytes", sa.Integer(), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("detected_columns", sa.Text(), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
    )
    op.create_index("ix_datasets_uploaded_by", "datasets", ["uploaded_by"])

    op.create_table(
        "audit_log",
        sa.Column("audit_id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("dataset_id", sa.Integer(), sa.ForeignKey("datasets.dataset_id", ondelete="SET NULL"), nullable=True),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("ix_audit_log_user_id", "audit_log", ["user_id"])
    op.create_index("ix_audit_log_dataset_id", "audit_log", ["dataset_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_dataset_id", table_name="audit_log")
    op.drop_index("ix_audit_log_user_id", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index("ix_datasets_uploaded_by", table_name="datasets")
    op.drop_table("datasets")
    op.drop_index("ix_candidate_zones_is_suitable", table_name="candidate_zones")
    op.drop_index("ix_candidate_zones_is_recommended", table_name="candidate_zones")
    op.drop_index("ix_candidate_zones_priority_label", table_name="candidate_zones")
    op.drop_index("ix_candidate_zones_district_id", table_name="candidate_zones")
    op.drop_table("candidate_zones")
    op.drop_index("ix_recycling_points_geometry", table_name="recycling_points")
    op.drop_index("ix_recycling_points_district_id", table_name="recycling_points")
    op.drop_table("recycling_points")
    op.drop_index("ix_waste_generation_district_id", table_name="waste_generation")
    op.drop_table("waste_generation")
    op.drop_index("ix_socioeconomic_indicators_district_id", table_name="socioeconomic_indicators")
    op.drop_table("socioeconomic_indicators")
    op.drop_index("ix_districts_geometry", table_name="districts")
    op.drop_table("districts")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
