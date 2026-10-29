"""Public synthetic input only; no VINs, real people, listings or vehicle photos."""
from pathlib import Path
from PIL import Image, ImageDraw


def seed(db, photos_dir):
    rows=[('Toyota','Camry',2015,630000),('Kia','Rio',2018,470000),('Hyundai','Solaris',2017,520000),('Renault','Duster',2016,610000),('Lada','Vesta',2019,490000),('Skoda','Octavia',2014,580000)]
    ids=[]
    for i,(make,model,year,price) in enumerate(rows,1):
        lot_id,_=db.upsert_lot(dict(efrsb_id=f'demo-{i}',title=f'{make} {model}, {year} — ДЕМО',make=make,model=model,year=year,make_model=f'{make} {model}',description='Синтетический лот. Условные царапины кузова по учебному описанию. Работоспособность агрегатов неизвестна. Изображение — тестовая таблица, не фотография автомобиля. Данные не относятся к настоящему имуществу.',price_amount=price,current_price=price,start_price=price+150000,price_currency='RUB',region='Нижегородская область',category='Легковые автомобили',trade_type='Публичное предложение',status='published',source_url='https://example.invalid/demo-lot'))
        ids.append(lot_id)
    folder=Path(photos_dir);folder.mkdir(parents=True,exist_ok=True)
    im=Image.new('RGB',(1024,600),'#edf2fa');draw=ImageDraw.Draw(im)
    for i,color in enumerate(['#225bd1','#71a5ee','#b3c9e8','#526374','#d6dce3']):
        draw.rectangle((50+i*185,140,215+i*185,360),fill=color)
    draw.text((55,55),'SYNTHETIC TEST IMAGE',fill='#18324d',font_size=34)
    draw.text((55,420),'NOT A VEHICLE PHOTO',fill='#18324d',font_size=34)
    draw.text((55,480),'No vehicle condition can be inferred from this image.',fill='#526374',font_size=23)
    im.save(folder/'demo-test.png')
    db.add_photo(ids[0],'https://example.invalid/synthetic-test',local_path='photos/demo-test.png',thumb_path='photos/demo-test.png',caption='Тестовая таблица, не фотография автомобиля',width=1024,height=600,status='ok',source_site='Синтетическая тестовая таблица')
    return ids
