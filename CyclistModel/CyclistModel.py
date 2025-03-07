# -*- coding: utf-8 -*-
"""
Created on Thu Apr 21 16:16:46 2022

@author: heath
"""

import os, sys
import numpy as np
from math import *
from trafficintelligence import moving
import gc
from shapely.geometry import *
from shapely.ops import nearest_points
from shapely.affinity import rotate


if 'SUMO_HOME' in os.environ:
     tools = os.path.join(os.environ['SUMO_HOME'], 'tools')
     sys.path.append(tools)
else:
     sys.exit("please declare environment variable 'SUMO_HOME'")

if os.name == 'posix':
    sumoCmd = ['sumo-gui','-c', '../data/test_track.sumocfg']
elif os.name == 'nt':
    sumoCmd = ['sumo-gui.exe','-c', '../data/test_track.sumocfg']
else:
    print('OS not supported!')

import traci

       
class roadUser(object):
    '''class to hold information about the road users (controlled and not)'''
    def __init__(self, ID, controlled, t=0):
        self.id = ID
        self.type = traci.vehicle.getVehicleClass(self.id)
        self.P = Point(traci.vehicle.getPosition(self.id)[0],traci.vehicle.getPosition(self.id)[1])                  #current position
        self.N = moving.NormAngle(traci.vehicle.getSpeed(self.id),(pi/2)-(traci.vehicle.getAngle(self.id)*pi/180))                                                                                  #current direction (norm angle)
        self.L = traci.vehicle.getLength(self.id)
        self.W = traci.vehicle.getWidth(self.id)                                                             
        self.Poly = self.getPoly()
        self.controlled = controlled
        self.lane = traci.vehicle.getLaneID(self.id)
        self.t = t
        
        
    def defineControlled(self, G):
        self.wish = min(max(np.random.normal(loc=5.3, scale=1.4, size=1),3.0),8.0)
        self.Tv = min(max(np.random.normal(loc=3, scale=1, size=1),2.0),4)
        self.Rv = min(max(np.random.normal(loc=6, scale=0.1, size=1),4),8)
        self.gv = min(max(np.random.normal(loc=1, scale=0.1, size=1),0.97),1.05)
        self.safety = min(max(np.random.normal(loc=0.25, scale=0.1, size=1),0.2),0.3)  
        self.comfortTheta = 0.5
        self.maxTheta = 0.7
        self.maxAcc = 1
        self.maxDec = -2
        self.G = G
        self.Traj = []
        traci.vehicle.setColor(self.id, (16,89,209,255) )   
        
        
    def getPoly(self):   
        #returns diamond for Type='bicycle' and rectangle for Type='car'    
        Line=LineString([(self.P.x-self.L*np.cos(self.N.angle), self.P.y-self.L*np.sin(self.N.angle)),(self.P.x,self.P.y)])
        right=Line.parallel_offset(0.5*self.W,'right')
        left=Line.parallel_offset(0.5*self.W,'left')
        if self.type == 'bicycle':
            return MultiPoint([Line.coords[0],list(right.centroid.coords)[0],Line.coords[-1],list(left.centroid.coords)[0]]).convex_hull.buffer(0.3)
        else:
            return MultiPoint([list(right.coords)[0],list(right.coords)[-1],list(left.coords)[0],list(left.coords)[-1]]).convex_hull.buffer(0.3)

    
    def findDirection(self):
        P = self.G.interpolate(self.G.project(self.P)+2*self.wish)
        direction = atan2(P.y-self.P.y,P.x-self.P.x)
        return direction

    
    def findAction(self, interaction_matrix, row, obstacles):  
        
        wish = moving.NormAngle(self.wish, self.findDirection()).getPoint()
        acc_interactors = moving.Point(0,0)
        acc_obstacles = moving.Point(0,0)
        Ap = 1
        
        for interactor in range(len(interaction_matrix[0,row,:])):
            if interactor != row and interaction_matrix[5,row,interactor] > 0:
                distance = interaction_matrix[6,row,interactor]
                if distance < 10:
                    u_bq = moving.Point(interaction_matrix[0,row,interactor],interaction_matrix[1,row,interactor])
                    similarity = interaction_matrix[2,row,interactor]
                    long_dist = interaction_matrix[3,row,interactor]
                    lat_dist = interaction_matrix[4,row,interactor]

                    D_star = distance
                    acc_interactors = acc_interactors.__add__(u_bq.__mul__(np.exp(-D_star/self.Rv))) 
            
            
        for obstacle in range(len(obstacles[0,row,:])):
            distance = obstacles[0,row,obstacle]
            a_comp = moving.Point(obstacles[1,row,obstacle],obstacles[2,row,obstacle])
            if distance < self.safety:
                d = 1       
            elif distance < self.safety + self.W/2:
                ds = self.safety + self.W/2
                d = -(distance-ds)/ds
            else:
                d = 0
            acc_obstacles = acc_obstacles.__add__(a_comp.__mul__(d))

        acc = wish.__sub__(self.N.getPoint()).divide(self.Tv).__sub__(acc_interactors.__mul__(Ap)).__sub__(acc_obstacles.__mul__(Ap))
        
        if self.N.norm < 0.5:
            nor = moving.NormAngle(0,0).fromPoint(acc)
            nor.angle = nor.angle * self.N.norm**2        
            acc = nor.getPoint()
        
        return acc
    

class roadUserSet(object):
    def __init__(self, all_road_users, RU_set = {}, controlled = [], interactionMatrix = np.zeros((0,0,0)), RUPositions=[]):
        self.all_road_users_SUMO = all_road_users
        self.RU_set = RU_set
        self.controlled = controlled
        self.interactionMatrix = interactionMatrix
        self.RUPositions = RUPositions
        
        self.createRoadUserSet()
        
        
    def createRoadUserSet(self):
        '''initiate set of road users at the beginning of a sumo simulation'''
        for row in self.all_road_users_SUMO:
            self.RU_set[row] = roadUser(row,False)
 
    
    def updateRoadUserSet(self, all_road_users_SUMO):
        '''check which road users to add/remove from road user set'''
        self.all_road_users_SUMO = all_road_users_SUMO
        enters = list(set(self.all_road_users_SUMO) - set(self.RU_set.keys()))
        exiters = list(set(self.RU_set.keys()) - set(self.all_road_users_SUMO))
        for row in exiters:
            self.RU_set.pop(row)
        for row in enters:
            if traci.vehicle.getSpeed(row) != 0 and traci.vehicle.getRoadID(row) == traci.vehicle.getRoute(row)[0] and traci.vehicle.getLanePosition(row)>5 and traci.vehicle.getVehicleClass(row) == 'bicycle':
                lane = traci.vehicle.getLaneID(row)
                guideline = traci.lane.getShape(lane)
                offset = np.random.uniform(-1, 1)
                self.RU_set[row] = roadUser(row,True)
                if offset > 0:
                    self.RU_set[row].defineControlled(LineString(guideline).parallel_offset(0.3*offset,'left'))
                else:
                    self.RU_set[row].defineControlled(rotate(LineString(guideline).parallel_offset(-0.3*offset,'right'),180,'centroid'))
        
    
    def updateControlled(self):
        for ID, RU in self.RU_set.items():
            try: 
                lane = traci.vehicle.getLaneID(ID)

                if RU.type == 'bicycle':
                    if traci.vehicle.getRoadID(ID) == traci.vehicle.getRoute(ID)[-1]:
                        RU.controlled = False
                    elif lane != RU.lane:
                        guideline = traci.lane.getShape(lane)
                        RU.defineControlled(LineString(guideline))
            
            except:
                continue

                    
        self.controlled = [RU.id for ID,RU in self.RU_set.items() if RU.controlled == True]        
    
    
    def updateRoadUserInformation(self, obstacles):
        '''update the position, direction, polygon of uncontrolled road users in set'''
        step=5
        self.RUPositions = sorted(self.RU_set.keys(), key=lambda ID: self.RU_set[ID].id)
        for ID, RU in self.RU_set.items():
            if RU.controlled == False or RU.t < 5:
                RU.P = Point(traci.vehicle.getPosition(ID)[0],traci.vehicle.getPosition(ID)[1])  
                RU.N = moving.NormAngle(traci.vehicle.getSpeed(ID),(pi/2)-(traci.vehicle.getAngle(ID)*pi/180))
                RU.Poly = RU.getPoly()
            else:
                acc = RU.findAction(self.interactionMatrix, self.RUPositions.index(ID), obstacles)
                V = RU.N.getPoint().__add__(acc)
                RU.P = Point(RU.P.x + V.x/step,RU.P.y + V.y/step)
                RU.N = moving.NormAngle(0,0).fromPoint(V)
                RU.Poly = RU.getPoly()
                
                traci.vehicle.moveToXY(RU.id, '', 0, RU.P.x, RU.P.y, (pi/2-RU.N.angle)*180/pi, keepRoute=2)
                traci.vehicle.setSpeed(RU.id,RU.N.norm)  
                
            RU.t+=1
            
       
    def distanceMatrix(self):
        '''Creates a symmetrical matrix of the distances between all road users'''
        mat = np.zeros((len(self.all_road_users_SUMO),len(self.all_road_users_SUMO)))
        for row in range(0,len(self.all_road_users_SUMO)-1):
            m = self.all_road_users_SUMO[row]
            P1 = Point(traci.vehicle.getPosition(m)[0],traci.vehicle.getPosition(m)[1])
            for col in range(row+1, len(self.all_road_users)):
                n = self.all_road_users_SUMO[col]
                P2 = Point(traci.vehicle.getPosition(n)[0],traci.vehicle.getPosition(n)[1])
                mat[row,col] = P1.distance(P2)        
        self.distance_matrix = mat + mat.T - np.diag(np.diag(mat))   
             
    
    def getEffectiveNormAngle(self, NormAngle):
        '''create speed to enable accurate conversion to the x,y format of the velocity'''
        if NormAngle.norm == 0:
            return moving.NormAngle(0.1,NormAngle.angle)
        else:
            return NormAngle
    
    
    def getTheta(self, u_bq, NPoint):
        cosine = np.round(moving.Point.cosine(u_bq,NPoint),8)
        theta = acos(cosine)
        if theta == 0:
            return -0.0001
        else:
            return theta
    
    
    def interactionDataMatrix(self):
        '''
        Creates a matrix of the parameters for the NOMAD model
            0: u_bq(t).x                                #vector from ego road user to interactor u_bq(t)=(P_q(t)-P_b(t))/(d_bq(t))   
            1: u_bq(t).y
            2: similarity                               #number between -1 and 1 desribing the similarity in velocity of two road users
            3: longitudinal distance between road users (headway)
            4: lateral distance between road user
            5: ahead (>0 if ahead, <0 if behind, 0 if beside)
            6: distance
        '''
        self.interactionMatrix = np.zeros((7,len(self.all_road_users_SUMO),len(self.all_road_users_SUMO)))
        row = 0
        for ID in sorted(self.RU_set.keys(), key=lambda ID: self.RU_set[ID].id):
            ego = self.RU_set[ID]
            egoNormAngle = self.getEffectiveNormAngle(ego.N)
            ego_point = Point(ego.P.x, ego.P.y)
            col = 0
            for num in sorted(self.RU_set.keys(), key=lambda ID: self.RU_set[ID].id):
                interactor = self.RU_set[num]
                if num != ID:
                    interactorNormAngle = self.getEffectiveNormAngle(interactor.N)
                    interactor_point = interactor.Poly.boundary.interpolate(interactor.Poly.boundary.project(ego.P))
                    distance = moving.Point.distanceNorm2(moving.Point(interactor_point.x, interactor_point.y), ego.P)
                    if distance < 10 :
                        u_bq = (moving.Point(interactor_point.x, interactor_point.y)-ego.P).__mul__(1/distance)
                        self.interactionMatrix[0,row,col] = u_bq.x
                        self.interactionMatrix[1,row,col] = u_bq.y
                        side = moving.Point.cross(egoNormAngle.getPoint(),u_bq)/(u_bq.norm2()*egoNormAngle.norm*sin(self.getTheta(u_bq,egoNormAngle.getPoint())))
                        self.interactionMatrix[2,row,col] = moving.Point.cosine(interactorNormAngle.getPoint(),egoNormAngle.getPoint())    #similarity
                        perp = moving.NormAngle(egoNormAngle.norm, egoNormAngle.angle+side*pi/2).getPoint()
                        self.interactionMatrix[3,row,col] = moving.Point.dot(u_bq,egoNormAngle.getPoint())/egoNormAngle.norm               #longitudinal distance
                        self.interactionMatrix[4,row,col] = moving.Point.dot(u_bq,perp)/egoNormAngle.norm                                  #lateral distance
                        self.interactionMatrix[5,row,col] = moving.Point.cosine(u_bq,egoNormAngle.getPoint())                              #ahead or behind
                        self.interactionMatrix[6,row,col] = distance
                col+=1
            row+=1
                

class obstacle(object):
    def __init__(self, ID, obstacleType, geometry):
        self.id = ID 
        self.type = obstacleType
        self.geometry = geometry
        

class obstacleSet(object):
    def __init__(self, obstacleList, obstacles = {}, interactionMatrix = np.zeros((0,0,0))):
        self.obstacleList = obstacleList
        self.obstacles = obstacles
        self.interactionMatrix = interactionMatrix


    def loadObstacles(self):
        for f in self.obstacleList:
            shape = LineString(traci.polygon.getShape(f))
            self.obstacles[f] = obstacle(f, 'obstacle', shape.buffer(0.1))
            
    
    def getEffectiveNormAngle(self, NormAngle):
        '''create speed to enable accurate conversion to the x,y format of the velocity'''
        if NormAngle.norm == 0:
            return moving.NormAngle(0.1,NormAngle.angle)
        else:
            return NormAngle
            
    
    def getTheta(self, u_bq, NPoint):
        try:
            theta = acos(moving.Point.cosine(u_bq,NPoint))
        except:
            theta = acos(moving.Point.cosine(u_bq,NPoint)+0.005) 
        if theta == 0:
            return -0.0001
        else:
            return theta 
    
    
    def getInteractionMatrix(self, RU_dictionary):
        self.interactionMatrix = np.zeros((3,len(RU_dictionary.keys()),len(self.obstacles.keys())))
        row = 0
        for ID in sorted(RU_dictionary.keys(), key=lambda ID: RU_dictionary[ID].id):
            RU = RU_dictionary[ID]
            egoNormAngle = self.getEffectiveNormAngle(RU.N)
            col = 0
            for num, obstacle in self.obstacles.items():
                obstacle_point = obstacle.geometry.boundary.interpolate(obstacle.geometry.boundary.project(RU.P))   
                distance = moving.Point(obstacle_point.x-RU.P.x,obstacle_point.y-RU.P.y).norm2()
                u_bq = moving.Point(obstacle_point.x-RU.P.x,obstacle_point.y-RU.P.y)
                u_bq = u_bq.__mul__(1/u_bq.norm2())
                theta = self.getTheta(u_bq, egoNormAngle.getPoint())
                side = moving.Point.cross(egoNormAngle.getPoint(),u_bq)/(u_bq.norm2()*egoNormAngle.norm*sin(theta))
                perp = moving.NormAngle(egoNormAngle.norm, egoNormAngle.angle+side*pi/2).getPoint()
                a_comp = perp.__mul__(1/perp.norm2())
                self.interactionMatrix[0,row,col] = distance
                self.interactionMatrix[1,row,col] = a_comp.x
                self.interactionMatrix[2,row,col] = a_comp.y
                col+=1
            row+=1
    
       
       
class simulation(object):
    def __init__(self):

        traci.start(sumoCmd)
        
        obstacles = obstacleSet(traci.polygon.getIDList())
        obstacles.loadObstacles()
        roadUsers = roadUserSet(traci.vehicle.getIDList()) 
        
        clock = 0
        while clock < 10000:
            
            roadUsers.updateRoadUserSet(traci.vehicle.getIDList())
            roadUsers.updateControlled()
            roadUsers.interactionDataMatrix()
            obstacles.getInteractionMatrix(roadUsers.RU_set)
            roadUsers.updateRoadUserInformation(obstacles.interactionMatrix)
              
            traci.simulationStep()
            clock+= 1
            gc.collect()

        traci.close()
    

simulation()




