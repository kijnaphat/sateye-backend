import os
import shutil
import tempfile
import zipfile
import numpy as np
import rasterio
from rasterio.windows import Window
from rasterio.features import shapes
from shapely.geometry import shape, mapping
import geopandas as gpd
from fastapi import FastAPI, UploadFile, File, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import List

# --- Configuration ---
app = FastAPI()

# --- CORS Configuration (สำคัญมากสำหรับการนำขึ้นออนไลน์) ---
# อนุญาตให้เว็บอื่นๆ เรียกใช้ API นี้ได้
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # ใน Production จริงๆ ควรเปลี่ยน * เป็น URL ของ Frontend เรา
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

TILE_SIZE = 480
MODEL_PATH = "model/result.tar.gz" # ต้องแน่ใจว่าไฟล์นี้มีอยู่จริงใน folder model/

# --- Mock AI Model Class ---
class CloudShadowModel:
    def __init__(self, model_path):
        print(f"Loading model from {model_path}...")
        pass

    def predict(self, image_tensor):
        # Mock logic: Simple brightness thresholding for demo
        # image_tensor shape: (4, 480, 480)
        avg = np.mean(image_tensor, axis=0)
        mask = np.zeros_like(avg, dtype=np.uint8)
        mask[avg > 200] = 1 # Cloud
        mask[avg < 50] = 2  # Shadow
        return mask

# Initialize Model
# หมายเหตุ: ถ้ายังไม่มีไฟล์ model ให้ comment บรรทัดนี้ก่อน deploy 
# หรือต้อง upload ไฟล์ model ไปด้วยใน folder model/
try:
    if os.path.exists(MODEL_PATH):
        ai_model = CloudShadowModel(MODEL_PATH)
    else:
        print(f"Warning: Model not found at {MODEL_PATH}. Using mock logic.")
        ai_model = CloudShadowModel("dummy")
except Exception as e:
    print(f"Error loading model: {e}. Using mock logic.")
    ai_model = CloudShadowModel("dummy")

def process_and_stitch(red_path, green_path, blue_path, nir_path, output_dir):
    with rasterio.open(red_path) as src:
        meta = src.meta.copy()
        height, width = src.height, src.width
        transform = src.transform
        crs = src.crs
        
    meta.update(count=1, dtype=rasterio.uint8, nodata=0)
    full_mask = np.zeros((height, width), dtype=np.uint8)

    with rasterio.open(red_path) as r_src, \
         rasterio.open(green_path) as g_src, \
         rasterio.open(blue_path) as b_src, \
         rasterio.open(nir_path) as n_src:

        for y in range(0, height, TILE_SIZE):
            for x in range(0, width, TILE_SIZE):
                window_h = min(TILE_SIZE, height - y)
                window_w = min(TILE_SIZE, width - x)
                window = Window(x, y, window_w, window_h)

                r = r_src.read(1, window=window)
                g = g_src.read(1, window=window)
                b = b_src.read(1, window=window)
                n = n_src.read(1, window=window)

                img_stack = np.stack([r, g, b, n])
                
                if window_h < TILE_SIZE or window_w < TILE_SIZE:
                    pad_h = TILE_SIZE - window_h
                    pad_w = TILE_SIZE - window_w
                    img_stack = np.pad(img_stack, ((0,0), (0, pad_h), (0, pad_w)), mode='reflect')

                prediction = ai_model.predict(img_stack)
                prediction = prediction[:window_h, :window_w]
                full_mask[y:y+window_h, x:x+window_w] = prediction

    output_tif = os.path.join(output_dir, "prediction.tif")
    with rasterio.open(output_tif, 'w', **meta) as dst:
        dst.write(full_mask, 1)

    return output_tif, full_mask, transform, crs

def generate_shapefile(mask, transform, crs, output_dir):
    results = (
        {'properties': {'class': v}, 'geometry': s}
        for i, (s, v) in enumerate(shapes(mask, mask=(mask > 0), transform=transform))
    )
    geoms = list(results)
    if not geoms:
        return None

    gdf = gpd.GeoDataFrame.from_features(geoms, crs=crs)
    class_map = {1: 'Cloud', 2: 'Shadow'}
    gdf['label'] = gdf['class'].map(class_map)
    
    shp_path = os.path.join(output_dir, "cloud_shadow_vectors.shp")
    gdf.to_file(shp_path)
    return shp_path

@app.post("/predict")
async def run_inference(
    red: UploadFile = File(...),
    green: UploadFile = File(...),
    blue: UploadFile = File(...),
    nir: UploadFile = File(...)
):
    temp_dir = tempfile.mkdtemp()
    try:
        file_paths = {}
        for name, file_obj in [('red', red), ('green', green), ('blue', blue), ('nir', nir)]:
            path = os.path.join(temp_dir, f"{name}.tif")
            with open(path, "wb") as buffer:
                shutil.copyfileobj(file_obj.file, buffer)
            file_paths[name] = path

        print("Starting processing...")
        tif_path, mask_array, transform, crs = process_and_stitch(
            file_paths['red'], file_paths['green'], file_paths['blue'], file_paths['nir'],
            temp_dir
        )
        
        print("Generating Shapefiles...")
        generate_shapefile(mask_array, transform, crs, temp_dir)

        zip_path = os.path.join(temp_dir, "results.zip")
        with zipfile.ZipFile(zip_path, 'w') as zipf:
            for root, dirs, files in os.walk(temp_dir):
                for file in files:
                    if file != "results.zip" and not file.endswith('.tif'): 
                         zipf.write(os.path.join(root, file), file)

        return FileResponse(zip_path, media_type='application/zip', filename='cloud_shadow_analysis.zip')

    except Exception as e:
        return {"error": str(e)}

@app.get("/")
def read_root():
    return {"status": "SatEye API is running"}