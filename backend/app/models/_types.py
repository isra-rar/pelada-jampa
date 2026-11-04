from enum import StrEnum

from sqlalchemy import Enum as SAEnum


def str_enum(enum_cls: type[StrEnum], length: int = 24) -> SAEnum:
    return SAEnum(
        enum_cls,
        native_enum=False,
        validate_strings=True,
        length=length,
        values_callable=lambda e: [x.value for x in e],
    )
