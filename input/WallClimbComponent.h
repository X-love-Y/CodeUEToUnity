// 示例 UE 源码: 爬墙检测组件
#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "WallClimbComponent.generated.h"

UCLASS(ClassGroup = (Custom), meta = (BlueprintSpawnableComponent))
class MYGAME_API UWallClimbComponent : public UActorComponent
{
    GENERATED_BODY()

public:
    UWallClimbComponent();

    virtual void TickComponent(float DeltaTime, ELevelTick TickType, FActorComponentTickFunction* ThisTickFunction) override;

    UFUNCTION(BlueprintCallable, Category = "Climb")
    bool TryStartClimb();

    UFUNCTION(BlueprintCallable, Category = "Climb")
    void StopClimb();

    UFUNCTION(BlueprintPure, Category = "Climb")
    bool IsOnClimbableSurface();

    UFUNCTION(BlueprintPure, Category = "Climb")
    FVector GetSurfaceNormal() const { return SurfaceNormal; }

protected:
    /** 判定可攀爬的最大墙面法线夹角(相对水平面) */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Climb")
    float MaxClimbableAngle = 45.0f;

    /** 检测前方墙面的距离 */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Climb")
    float TraceDistance = 80.0f;

    /** 攀爬高度上限, 超过后无法继续 */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Climb")
    float MaxClimbHeight = 400.0f;

private:
    bool bIsClimbing = false;
    FVector SurfaceNormal = FVector::ZeroVector;
    float ClimbStartZ = 0.0f;
    float ClimbTraceTimer = 0.0f;
};
