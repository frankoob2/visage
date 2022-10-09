import time
import uuid
from typing import List, Optional

import json
import numpy as np
from PIL import Image
from io import BytesIO
from annoy import AnnoyIndex
from deepface.commons import functions
from deepface.basemodels import Facenet512
from fastapi import FastAPI, UploadFile, HTTPException
from pydantic import BaseModel, Field

## Setup the app and load everything into memory
app = FastAPI()

# load the face model
vector_size = 512
model = Facenet512.loadModel()

input_shape_x, input_shape_y = functions.find_input_shape(model)

# setup the annoy index, nearest neighbors search
index = AnnoyIndex(vector_size, "euclidean")
index.load(f"face.db")

# load the stash performer index and annoy index, the annoy index maps to the id's in the .ann file
# the performers db holds stashdb performer id's and the performer name and image
ANNOY_INDEX = json.load(open(f"face.json"))
PERFORMER_DB = json.load(open("performers.json"))

## setup the models used in the API


class Performer(BaseModel):
    id: str = Field(..., title="The performer StashDB ID")
    name: Optional[str] = Field("N/A", description="Name of the performer")
    image: Optional[str] = Field(
        "N/A", description="Image URL of the performer. (if available in stashDB)"
    )
    distance: Optional[float] = Field(
        0.0, description="Distance from the query image. (lower is better)"
    )


class PerformerSearch(BaseModel):
    id: str = Field(..., title="The stashface ID")
    performers: List[Performer] = Field(
        ..., description="List of performers sorted by clostest distance"
    )

class Search(BaseModel):
    vector: List[float] = Field(..., description="The face vector")


## API endpoints


@app.post("/", name="recognise", response_model=PerformerSearch)
async def recognise(file: UploadFile):
    if not file.filename.endswith((".jpg", ".jpeg", ".png", ".webp")):
        raise HTTPException(
            status_code=400,
            detail="Invalid file type (only .jpg, .jpeg and .png are allowed)",
        )

    content = await file.read()

    try:
        image = Image.open(BytesIO(content))
    except Exception as e:
        print(str(e))
        raise HTTPException(status_code=400, detail="Invalid image file")

    if not image.mode == 'RGB':
        image = image.convert('RGB')

    image_array = np.array(image)

    t = time.time()
    try:
        img = functions.preprocess_face(
            img=image_array,
            target_size=(input_shape_x, input_shape_y),
            enforce_detection=True,
            detector_backend="retinaface",
            align=True,
        )

        img = functions.normalize_input(img, normalization="Facenet2018")
    except ValueError:
        raise HTTPException(status_code=409, detail="No face detected")
    print("Face detected in", time.time() - t)

    t = time.time()
    face = model.predict(img)[0].tolist()
    print("Face embedding in", time.time() - t)

    return lookup_performer(face)


@app.post("/search", name="search")
async def confirm(obj: Search):
    if len(obj.vector) != 512:
        raise HTTPException(status_code=400, detail="Invalid vector size")

    return lookup_performer(obj.vector)


def lookup_performer(vector):
    # create a unique id for the request
    uid = str(uuid.uuid4())

    t = time.time()
    ids, distances = index.get_nns_by_vector(vector, 50, search_k=10000, include_distances=True)
    print("Search done in", time.time() - t)

    persons = {}
    for p, distance in zip(ids, distances):
        id = ANNOY_INDEX[p].split("=")[0]
        if id in persons:
            persons[id]["hits"] += 1
            persons[id]["distance"] -= 0.5
            continue

        persons[id] = {
            "id": id,
            "distance": round(distance, 2),
            "hits": 1,
        }

        if id in PERFORMER_DB:
            person = PERFORMER_DB.get(id)
            persons[id]['name'] = person["name"]
            persons[id]['image'] = person["image"]

    return {
        "id": uid,
        "performers": sorted(persons.values(), key=lambda x: x["distance"])[:10],
    }
