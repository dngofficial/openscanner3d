"""Summarize feature support and verified view connectivity."""
import json
from pathlib import Path
import re
import sqlite3
import statistics


def log_summary(text):
    counts=[int(v) for v in re.findall(r"Features:\s+(\d+)",text)]
    connected=re.findall(r"\(connected\s+(\d+),\s*loaded\s+\d+\)",text)
    result={"initial_pair_failure":any(s in text for s in
            ("No good initial image pair found","Discarding reconstruction due to bad initial pair"))}
    if counts:
        result.update(images=len(counts),features_min=min(counts),features_median=statistics.median(counts),
                      features_max=max(counts),features_per_image=counts)
    if connected: result["connected_images"]=int(connected[-1])
    return result


def database_summary(path):
    path=Path(path)
    if not path.is_file(): return {}
    try:
        connection=sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True)
        try:
            rows=connection.execute("SELECT images.image_id, images.name, COALESCE(keypoints.rows,0) "
                                    "FROM images LEFT JOIN keypoints ON images.image_id=keypoints.image_id").fetchall()
            pairs=connection.execute('SELECT pair_id, rows FROM two_view_geometries WHERE rows>=15').fetchall()
        finally: connection.close()
    except sqlite3.Error:
        return {}
    graph={image_id:set() for image_id,_,_ in rows}
    usable=0
    for pair_id,inliers in pairs:
        second=pair_id%2147483647;first=(pair_id-second)//2147483647
        if first in graph and second in graph:
            graph[first].add(second);graph[second].add(first);usable+=1
    largest=0;remaining=set(graph)
    while remaining:
        component={remaining.pop()};pending=list(component)
        while pending:
            for other in graph[pending.pop()]:
                if other in remaining:
                    remaining.remove(other);component.add(other);pending.append(other)
        largest=max(largest,len(component))
    counts=[int(r[2]) for r in rows]
    return dict(images=len(rows),features_min=min(counts,default=0),
                features_median=statistics.median(counts) if counts else 0,features_max=max(counts,default=0),
                verified_pairs=usable,connected_images=sum(bool(neighbors) for neighbors in graph.values()),
                largest_connected_component=largest,
                features_per_image=[dict(name=name,features=count) for _,name,count in rows])


def collect(root,database=None):
    root=Path(root);log=root/"reconstruction.log"
    result=log_summary(log.read_text(encoding="utf-8",errors="replace") if log.is_file() else "")
    if database: result.update(database_summary(database))
    result["notes"]=[]
    if result.get("features_median",9999)<100:
        result["notes"].append("Very few image features. Preserve original image detail and camera-tracking background context.")
    if result.get("connected_images",result.get("images",0))<result.get("images",0)*.6:
        result["notes"].append("Most views are disconnected. Use smaller viewpoint changes, more overlap and a stationary scene.")
    (root/"diagnostics.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    return result


def alignment_failure_message(info):
    counts=""
    if "connected_images" in info and "images" in info:
        counts=f" Only {info['connected_images']} of {info['images']} views had verified connections."
    return ("Camera alignment could not find a valid starting image pair."+counts+
            " Preserve original tracking context, move the camera around a stationary object with smaller steps, "
            "and capture sharper overlapping views. See diagnostics.json.")
