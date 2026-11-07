"""CPU foreground isolation inside a user-selected region, plus mask cleanup."""
import cv2
import numpy as np


def clean_frame(bgr, roi=(.1,.08,.9,.92), background=True, denoise=True):
    image = cv2.bilateralFilter(bgr, 5, 25, 25) if denoise else bgr.copy()
    height, width = image.shape[:2]
    if not background:
        return image, np.full((height,width),255,np.uint8)
    x0,y0,x1,y1 = np.asarray(roi,float)
    if not np.isfinite([x0,y0,x1,y1]).all() or not (0<=x0<x1<=1 and 0<=y0<y1<=1):
        raise ValueError("Select a valid object rectangle.")
    small = cv2.resize(image, (min(width,320), max(1,round(height*min(width,320)/width))))
    h,w = small.shape[:2]
    left,top = max(1,int(x0*w)),max(1,int(y0*h))
    right,bottom = min(w-1,int(x1*w)),min(h-1,int(y1*h))
    if right-left<12 or bottom-top<12: raise ValueError("Object rectangle is too small.")
    labels = np.zeros((h,w),np.uint8)
    cv2.grabCut(small, labels, (left,top,right-left,bottom-top),
                np.zeros((1,65),np.float64),np.zeros((1,65),np.float64),3,cv2.GC_INIT_WITH_RECT)
    mask = np.uint8((labels==cv2.GC_FGD)|(labels==cv2.GC_PR_FGD))*255
    mask = cv2.morphologyEx(mask,cv2.MORPH_OPEN,np.ones((3,3),np.uint8))
    mask = cv2.morphologyEx(mask,cv2.MORPH_CLOSE,np.ones((5,5),np.uint8))
    n,components,stats,_ = cv2.connectedComponentsWithStats(mask)
    if n>1:
        # Keep substantial components; do not turn tiny color specks into surfaces.
        sizes=stats[1:,cv2.CC_STAT_AREA]
        keep=np.where(sizes>=max(20,.03*sizes.max()))[0]+1
        mask=np.uint8(np.isin(components,keep))*255
    mask=cv2.resize(mask,(width,height),interpolation=cv2.INTER_NEAREST)
    cleaned=image.copy()
    cleaned[mask==0]=127
    return cleaned,mask


def feature_mask(mask):
    return cv2.erode(mask,np.ones((5,5),np.uint8))
