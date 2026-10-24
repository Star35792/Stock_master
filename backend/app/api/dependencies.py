from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.database.positions_session import get_positions_db
from app.database.research_session import get_research_db
from app.database.session import get_db

DatabaseSession = Annotated[Session, Depends(get_db)]
PositionsDatabaseSession = Annotated[Session, Depends(get_positions_db)]
ResearchDatabaseSession = Annotated[Session, Depends(get_research_db)]
