"""Closed vocabularies shared by the crawler, the LLM prompts and the Django models.

Values are stored in the database and sent to the LLM, so never rename a value;
add a new one and migrate data instead.
"""

from enum import Enum


class QualificationLevel(str, Enum):
    """Minimum educational qualification, in ascending order."""

    CLASS_8 = "CLASS_8"
    CLASS_10 = "CLASS_10"
    CLASS_12 = "CLASS_12"
    ITI = "ITI"
    DIPLOMA = "DIPLOMA"
    GRADUATE = "GRADUATE"
    POSTGRADUATE = "POSTGRADUATE"
    DOCTORATE = "DOCTORATE"

    @property
    def rank(self) -> int:
        # ITI and DIPLOMA are post-10th tracks; ranking them between 12th and
        # graduation is good enough for a "likely eligible" filter.
        return list(QualificationLevel).index(self)


class Discipline(str, Enum):
    """Subject tags attached to a qualification. ANY means no subject restriction."""

    ANY = "ANY"
    ENGINEERING_ANY = "ENGINEERING_ANY"
    ENGINEERING_CIVIL = "ENGINEERING_CIVIL"
    ENGINEERING_MECHANICAL = "ENGINEERING_MECHANICAL"
    ENGINEERING_ELECTRICAL = "ENGINEERING_ELECTRICAL"
    ENGINEERING_ELECTRONICS = "ENGINEERING_ELECTRONICS"
    ENGINEERING_COMPUTER = "ENGINEERING_COMPUTER"
    SCIENCE = "SCIENCE"
    MATHEMATICS = "MATHEMATICS"
    STATISTICS = "STATISTICS"
    COMMERCE = "COMMERCE"
    ECONOMICS = "ECONOMICS"
    ARTS = "ARTS"
    LAW = "LAW"
    MEDICINE = "MEDICINE"
    NURSING = "NURSING"
    PHARMACY = "PHARMACY"
    AGRICULTURE = "AGRICULTURE"
    CHARTERED_ACCOUNTANCY = "CHARTERED_ACCOUNTANCY"
    COMPANY_SECRETARY = "COMPANY_SECRETARY"
    COST_ACCOUNTANCY = "COST_ACCOUNTANCY"
    MANAGEMENT = "MANAGEMENT"
    EDUCATION = "EDUCATION"
    OTHER = "OTHER"


class ReservationCategory(str, Enum):
    """Vertical reservation categories; vacancies by these add up to the post total."""

    UR = "UR"
    OBC = "OBC"
    SC = "SC"
    ST = "ST"
    EWS = "EWS"


class RelaxationCategory(str, Enum):
    """Groups that get upper-age relaxation."""

    OBC = "OBC"
    SC = "SC"
    ST = "ST"
    PWBD_UR = "PWBD_UR"
    PWBD_OBC = "PWBD_OBC"
    PWBD_SC_ST = "PWBD_SC_ST"
    EX_SERVICEMEN = "EX_SERVICEMEN"


class FeeApplicant(str, Enum):
    """Who a fee rule applies to."""

    ALL = "ALL"
    UR = "UR"
    OBC = "OBC"
    EWS = "EWS"
    SC = "SC"
    ST = "ST"
    PWBD = "PWBD"
    EX_SERVICEMEN = "EX_SERVICEMEN"
    FEMALE = "FEMALE"


class GenderRestriction(str, Enum):
    ANY = "ANY"
    MALE = "MALE"
    FEMALE = "FEMALE"


class DocumentKind(str, Enum):
    NOTIFICATION = "NOTIFICATION"
    CORRIGENDUM = "CORRIGENDUM"


class State(str, Enum):
    """States and union territories. A recruitment with no state is a central one."""

    ANDAMAN_AND_NICOBAR_ISLANDS = "ANDAMAN_AND_NICOBAR_ISLANDS"
    ANDHRA_PRADESH = "ANDHRA_PRADESH"
    ARUNACHAL_PRADESH = "ARUNACHAL_PRADESH"
    ASSAM = "ASSAM"
    BIHAR = "BIHAR"
    CHANDIGARH = "CHANDIGARH"
    CHHATTISGARH = "CHHATTISGARH"
    DADRA_AND_NAGAR_HAVELI_AND_DAMAN_AND_DIU = "DADRA_AND_NAGAR_HAVELI_AND_DAMAN_AND_DIU"
    DELHI = "DELHI"
    GOA = "GOA"
    GUJARAT = "GUJARAT"
    HARYANA = "HARYANA"
    HIMACHAL_PRADESH = "HIMACHAL_PRADESH"
    JAMMU_AND_KASHMIR = "JAMMU_AND_KASHMIR"
    JHARKHAND = "JHARKHAND"
    KARNATAKA = "KARNATAKA"
    KERALA = "KERALA"
    LADAKH = "LADAKH"
    LAKSHADWEEP = "LAKSHADWEEP"
    MADHYA_PRADESH = "MADHYA_PRADESH"
    MAHARASHTRA = "MAHARASHTRA"
    MANIPUR = "MANIPUR"
    MEGHALAYA = "MEGHALAYA"
    MIZORAM = "MIZORAM"
    NAGALAND = "NAGALAND"
    ODISHA = "ODISHA"
    PUDUCHERRY = "PUDUCHERRY"
    PUNJAB = "PUNJAB"
    RAJASTHAN = "RAJASTHAN"
    SIKKIM = "SIKKIM"
    TAMIL_NADU = "TAMIL_NADU"
    TELANGANA = "TELANGANA"
    TRIPURA = "TRIPURA"
    UTTAR_PRADESH = "UTTAR_PRADESH"
    UTTARAKHAND = "UTTARAKHAND"
    WEST_BENGAL = "WEST_BENGAL"


class Confidence(str, Enum):
    """How a draft was published. Decided by sarkari_core.quality.assess, never by a person."""

    HIGH = "HIGH"  # passed every check
    MEDIUM = "MEDIUM"  # published; some fields marked "verify on the official notice"
    LOW = "LOW"  # header only (dates and official link); the posts couldn't be extracted


class Sector(str, Enum):
    """Drives the sector landing pages (/railway-jobs, /bank-jobs, ...)."""

    RAILWAY = "RAILWAY"
    BANKING = "BANKING"
    DEFENCE = "DEFENCE"
    POLICE = "POLICE"
    CIVIL_SERVICES = "CIVIL_SERVICES"
    STAFF_SELECTION = "STAFF_SELECTION"
    TEACHING = "TEACHING"
    HEALTH = "HEALTH"
    PSU = "PSU"
    OTHER = "OTHER"


# Upper-age relaxation for central government posts, applied when a notification
# doesn't state its own. Each recruitment (or post) can override this table.
# Ex-servicemen relaxation depends on length of service, so it has no default.
CENTRAL_DEFAULT_AGE_RELAXATION: dict[RelaxationCategory, int] = {
    RelaxationCategory.OBC: 3,
    RelaxationCategory.SC: 5,
    RelaxationCategory.ST: 5,
    RelaxationCategory.PWBD_UR: 10,
    RelaxationCategory.PWBD_OBC: 13,
    RelaxationCategory.PWBD_SC_ST: 15,
}
