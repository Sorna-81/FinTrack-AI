from datetime import date
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


def validate_bcrypt_password(value: str) -> str:
    # bcrypt only considers the first 72 bytes; rejecting longer values avoids
    # silently treating distinct passwords as identical.
    if len(value.encode("utf-8")) > 72:
        raise ValueError("Password must be no more than 72 UTF-8 bytes")
    return value


class Register(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)

    _password_limit = field_validator("password")(validate_bcrypt_password)


class Login(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)

    _password_limit = field_validator("password")(validate_bcrypt_password)


class TransactionIn(BaseModel):
    amount: float = Field(gt=0, allow_inf_nan=False)
    category: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    kind: str = Field(pattern="^(income|expense)$")
    date: date
    payment_method: str = Field(default="Card", max_length=40)


class TransactionOut(TransactionIn):
    id: int
    model_config = ConfigDict(from_attributes=True)


class BudgetIn(BaseModel):
    category: str = "All categories"
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    amount: float = Field(gt=0, allow_inf_nan=False)


class GoalIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    target_amount: float = Field(gt=0, allow_inf_nan=False)
    saved_amount: float = Field(default=0, ge=0, allow_inf_nan=False)
    target_date: date | None = None
